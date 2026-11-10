"""Apple TV adapter against a fake pyatv (scan / connect / pair / push), no network."""
import asyncio
import types

import pytest

from fakes import dev
from strivo.adapters import appletv as atv_mod
from strivo.adapters.appletv import AppleTVAdapter, playing_to_np
from strivo.adapters.base import OFF, PAUSED, PLAYING

pyatv = pytest.importorskip("pyatv")
from pyatv.const import DeviceState, MediaType, OperatingSystem, PowerState, Protocol  # noqa: E402


def playing(**kw):
    base = dict(device_state=DeviceState.Playing, media_type=MediaType.Video, title="Career Day",
                series_name="Abbott Elementary", season_number=3, episode_number=4, artist=None, album=None,
                genre=None, position=120, total_time=1320)
    base.update(kw)
    return types.SimpleNamespace(**base)


class FakeATV:
    def __init__(self):
        self.launched = []
        self.closed = False
        self.metadata = types.SimpleNamespace(app=types.SimpleNamespace(name="Hulu", identifier="com.hulu.plus"))

        async def _playing():
            return playing()
        self.metadata.playing = _playing
        self.power = types.SimpleNamespace(power_state=PowerState.On, listener=None)
        self.push_updater = types.SimpleNamespace(listener=None, start=lambda: None)
        self.listener = None

        async def launch_app(x):
            self.launched.append(x)
        self.apps = types.SimpleNamespace(launch_app=launch_app)

    def close(self):
        self.closed = True


class FakeConf:
    def __init__(self, name="Living Room", os_=OperatingSystem.TvOS, protocols=(Protocol.AirPlay, Protocol.Companion),
                 model="Apple TV 4K (gen 3)"):
        self.name, self.address, self.identifier = name, "10.0.0.5", "AA:BB"
        self.device_info = types.SimpleNamespace(model_str=model, version="18.1", operating_system=os_)
        self.services = [types.SimpleNamespace(protocol=p, credentials=None,
                                               pairing=types.SimpleNamespace(name="Mandatory")) for p in protocols]

    def get_service(self, proto):
        return next((s for s in self.services if s.protocol == proto), None)


@pytest.fixture
def fake_pyatv(monkeypatch):
    state = dict(atv=FakeATV(), confs=[FakeConf(), FakeConf("Kitchen speaker", OperatingSystem.TvOS, (Protocol.RAOP,)),
                                       FakeConf("HomePod", model="HomePod mini"), FakeConf("Office Mac", OperatingSystem.MacOS)],
                 pins=[])

    async def scan(loop, hosts=None, identifier=None, timeout=5, storage=None):
        return state["confs"] if not hosts and not identifier else state["confs"][:1]

    async def connect(conf, loop, storage=None):
        return state["atv"]

    class Handler:
        device_provides_pin = True
        has_paired = False

        def __init__(self, proto):
            self.proto = proto

        async def begin(self):
            pass

        def pin(self, p):
            state["pins"].append((self.proto, p))

        async def finish(self):
            self.has_paired = True

        async def close(self):
            pass

    async def pair(conf, proto, loop, storage=None, name=None):
        return Handler(proto)

    monkeypatch.setattr(atv_mod.pyatv, "scan", scan)
    monkeypatch.setattr(atv_mod.pyatv, "connect", connect)
    monkeypatch.setattr(atv_mod.pyatv, "pair", pair)
    return state


def test_playing_to_np_maps_fields():
    np = playing_to_np(playing(device_state=DeviceState.Paused),
                       types.SimpleNamespace(name="Max", identifier="com.wbd.stream"))
    assert np.state == PAUSED and np.app == "HBO Max" and np.app_id == "com.wbd.stream"
    assert (np.series, np.season, np.episode, np.position, np.duration) == ("Abbott Elementary", 3, 4, 120, 1320)


async def test_discover_keeps_only_tvos_with_control_protocols(data, fake_pyatv):
    found = await AppleTVAdapter(data).discover(1)
    assert [d.name for d in found] == ["Living Room"]
    assert found[0].model == "Apple TV 4K (gen 3)" and found[0].os_version == "18.1"


async def test_pair_asks_for_both_codes(data, fake_pyatv):
    asked = []

    async def prompt(msg, need_input=True):
        asked.append(msg)
        return "1234"
    d = await AppleTVAdapter(data).pair(dev("appletv", "Living Room", host="10.0.0.5"), prompt)
    assert len(asked) == 2 and "now playing" in asked[0] and "Play on TV" in asked[1]
    assert [p for _, p in fake_pyatv["pins"]] == [1234, 1234]
    assert d.model == "Apple TV 4K (gen 3)" and d.id == "living-room"


async def test_now_playing_status_and_launch(data, fake_pyatv):
    ad = AppleTVAdapter(data)
    d = dev("appletv", host="10.0.0.5")
    np = await ad.now_playing(d)
    assert np.state == PLAYING and np.app == "Hulu" and np.title == "Career Day"
    st = await ad.status(d)
    assert st.online and st.power == "on" and st.app == "Hulu"
    await ad.launch(d, "com.hulu.plus")
    await ad.launch(d, "com.google.ios.youtube", "youtube://watch/abcdefghijk")
    assert fake_pyatv["atv"].launched == ["com.hulu.plus", "youtube://watch/abcdefghijk"]
    fake_pyatv["atv"].power.power_state = PowerState.Off
    assert (await ad.now_playing(d)).state == OFF


async def test_watch_pushes_updates_and_stops(data, fake_pyatv):
    ad = AppleTVAdapter(data)
    d = dev("appletv", host="10.0.0.5")
    got, stop = [], asyncio.Event()
    task = asyncio.create_task(ad.watch(d, lambda dv, np: got.append(np), 10, stop))
    await asyncio.sleep(0.05)
    listener = fake_pyatv["atv"].push_updater.listener
    listener.playstatus_update(None, playing(title="Pilot"))
    listener.powerstate_update(PowerState.On, PowerState.Off)
    stop.set()
    await asyncio.wait_for(task, 2)
    assert [g.title for g in got] == ["Pilot", None] and got[1].state == OFF
    assert fake_pyatv["atv"].closed


async def test_watch_raises_when_connection_lost(data, fake_pyatv):
    ad = AppleTVAdapter(data)
    task = asyncio.create_task(ad.watch(dev("appletv", host="10.0.0.5"), lambda *a: None, 10))
    await asyncio.sleep(0.05)
    fake_pyatv["atv"].listener.connection_lost(Exception("gone"))
    with pytest.raises(Exception, match="connection lost"):
        await asyncio.wait_for(task, 35)
