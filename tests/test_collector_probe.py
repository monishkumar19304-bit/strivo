import asyncio
import json

from conftest import read_events
from fakes import FakeAdapter, dev
from strivo.adapters.base import IDLE, NowPlaying, PLAYING
from strivo.collector import Collector
from strivo.probe import Probe, classify, from_events


async def test_collector_writes_only_changes(data):
    fake = FakeAdapter(script=[NowPlaying(state=PLAYING, app="Hulu", title="X | S1 E1 - A", position=p) for p in (1, 2, 3)]
                       + [NowPlaying(state=IDLE)])
    data.update_config(lambda c: c.__setitem__("devices", [dev("fake", "Den").to_dict()]))
    data.update_config(lambda c: c.__setitem__("poll_seconds", 0.02))
    col = Collector(data, {"fake": fake})
    await col.start()
    await asyncio.sleep(0.3)
    await col.stop()
    rows = read_events(data)
    plays = [r for r in rows if r["kind"] == "play"]
    assert [p["state"] for p in plays] == [PLAYING, IDLE]        # position ticks alone aren't written
    assert plays[0]["dev"] == "den" and plays[0]["src"] == "fake"
    assert col.live["den"]["online"]


async def test_collector_backs_off_on_errors(data):
    class Broken(FakeAdapter):
        async def now_playing(self, device):
            raise ConnectionError("unreachable")
    data.update_config(lambda c: c.__setitem__("devices", [dev("fake", "Den").to_dict()]))
    col = Collector(data, {"fake": Broken()})
    await col.start()
    await asyncio.sleep(0.2)
    await col.stop()
    rows = read_events(data)
    assert [r["kind"] for r in rows] == ["connected", "disconnected"]
    assert col.live["den"]["online"] is False and "unreachable" in col.live["den"]["error"]


def test_probe_classification_and_privacy():
    p = Probe()
    tv = dev("appletv", "Nick's Living Room", host="192.168.1.50", model="Apple TV 4K (gen 3)", os_version="18.1")
    fire = dev("android", "Kids Room", host="192.168.1.51", model="Amazon AFTKA", os_version="Fire OS (Android 9)",
               platform="firetv")
    p.on_np(tv, NowPlaying(state=PLAYING, app="Hulu", title="Secret Show | S1 E2 - Private Name", position=5, duration=60))
    p.on_np(tv, NowPlaying(state=PLAYING, app="Netflix"))
    p.on_np(tv, NowPlaying(state=PLAYING, app="YouTube TV", title="Local News at 6", artist="WXYZ"))
    p.on_np(fire, NowPlaying(state=PLAYING, app="Some App", app_id="com.some.app", title="A Personal Video Title"))
    p.on_np(fire, NowPlaying(state=IDLE))
    r = p.report(10)
    assert "Hulu: ✅ show + episode · position+duration" in r
    assert "Netflix: ⚪ app only" in r and "YouTube TV: ✅ live channel" in r
    assert "Some App [com.some.app]: 🟡 title only" in r
    assert "Box 1: Apple TV (Apple TV 4K (gen 3), 18.1)" in r and "Box 2: Fire TV" in r
    for secret in ("Secret Show", "Private Name", "Nick", "Living Room", "192.168", "Local News", "WXYZ",
                   "Personal Video", "Kids Room"):
        assert secret not in r, secret
    assert classify(dict(state=IDLE, app="Hulu", title="x")) is None


def test_probe_from_existing_events(tmp_path):
    f = tmp_path / "events.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in [
        dict(kind="play", dev="Living Room", app="Hulu", state="Playing", title="S | S1 E1 - A", ts="2026-01-01T20:00:00"),
        dict(kind="play", dev="Living Room", app="Netflix", state="Playing", title=None, ts="2026-01-01T21:00:00"),
        dict(kind="connected", dev="Living Room", ts="2026-01-01T19:00:00")]))
    r = from_events(str(f)).report()
    assert "Hulu: ✅ show + episode" in r and "Netflix: ⚪ app only" in r and "Living Room" not in r
