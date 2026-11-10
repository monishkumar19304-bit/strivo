"""Roku (fake ECP server), Jellyfin and Plex (fake HTTP APIs + webhook payloads)."""
import asyncio

import pytest

from fakes import dev, fake_json_server, fake_roku_server
from strivo.adapters.base import IDLE, OFF, PAUSED, PLAYING, AdapterError
from strivo.adapters.jellyfin import JellyfinAdapter
from strivo.adapters.plex import PlexAdapter
from strivo.adapters.roku import RokuAdapter, parse_media_player


# ------------------------------------------------------------------ Roku
@pytest.fixture
def roku():
    state = dict(app="Netflix", app_id="12", player_state="play", pos_ms=61000, dur_ms=1500000)
    srv = fake_roku_server(state)
    yield state, srv
    srv.close()


async def test_roku_now_playing_app_only_and_with_title(data, roku):
    state, srv = roku
    ad, d = RokuAdapter(data), dev("roku", "Den", port=srv.port)
    np = await ad.now_playing(d)
    assert (np.state, np.app, np.app_id, np.title, np.position, np.duration) == (PLAYING, "Netflix", "12", None, 61.0, 1500.0)
    state.update(title="Some Episode", player_state="pause")      # a channel that does share titles
    np = await ad.now_playing(d)
    assert np.title == "Some Episode" and np.state == PAUSED
    state.update(app_id=None)
    assert (await ad.now_playing(d)).state == IDLE              # home screen
    state.update(power="DisplayOff")
    assert (await ad.now_playing(d)).state == OFF


async def test_roku_pair_status_launch(data, roku):
    state, srv = roku
    ad, d = RokuAdapter(data), dev("roku", "Den", port=srv.port)
    d = await ad.pair(d, None)
    assert d.model == "Roku Roku Ultra" and d.os_version == "13.0.0" and d.identifier == "X00000000001"
    st = await ad.status(d)
    assert st.online and st.app == "Netflix"
    await ad.launch(d, "2285")
    await ad.launch(d, "837", "abcdefghijk")
    assert "/launch/2285" in state["posts"] and any(p.startswith("/launch/837?contentId=abcdefghijk") for p in state["posts"])
    with pytest.raises(AdapterError):
        await ad.launch(d, "12/../keypress/Home")


def test_roku_media_player_with_metadata_block():
    xml = """<player error="false" state="play"><plugin id="13535" name="Plex"/><position>5000 ms</position>
    <duration>60000 ms</duration><metadata><title>Pilot</title><series>Show</series><season>1</season><episode>2</episode></metadata></player>"""
    mp = parse_media_player(xml)
    assert (mp["title"], mp["series"], mp["season"], mp["episode"], mp["app"]) == ("Pilot", "Show", 1, 2, "Plex")


async def test_roku_unreachable_is_a_friendly_error(data):
    with pytest.raises(AdapterError, match="Permissive"):
        await RokuAdapter(data, timeout=1).pair(dev("roku", port=1), None)


# ------------------------------------------------------------------ Jellyfin
JF_SESSIONS = [
    {"DeviceName": "Living Room TV", "DeviceId": "abc", "Client": "Jellyfin Android TV", "UserName": "someone",
     "NowPlayingItem": {"Type": "Episode", "Name": "Episode 2", "SeriesName": "Strivo Test Show", "ParentIndexNumber": 1,
                        "IndexNumber": 2, "RunTimeTicks": 1200 * 10_000_000},
     "PlayState": {"PositionTicks": 300 * 10_000_000, "IsPaused": False}},
    {"DeviceName": "Phone", "DeviceId": "def", "Client": "Jellyfin Web"},   # not playing
]


async def test_jellyfin_pair_sessions_and_client_devices(data):
    srv = fake_json_server({"/System/Info": {"Id": "srv1", "ProductName": "Jellyfin Server", "Version": "10.10.3"},
                            "/Sessions": JF_SESSIONS}, require={"Authorization": 'Token="k3y"'})
    try:
        ad = JellyfinAdapter(data)
        server = dev("jellyfin", "Home Server", port=srv.port)
        prompts = []

        async def prompt(msg, need_input=True):
            prompts.append(msg)
            return "k3y"
        server = await ad.pair(server, prompt)
        assert server.identifier == "srv1" and server.os_version == "10.10.3" and "API key" in prompts[0]
        assert data.secret("jellyfin:home-server") == "k3y"
        s = await ad.sessions(server)
        assert len(s) == 1
        cdev, np = s[0]
        assert cdev.id == "home-server--living-room-tv" and cdev.name == "Living Room TV · Home Server"
        assert (np.series, np.season, np.episode, np.position, np.duration, np.state) == \
            ("Strivo Test Show", 1, 2, 300.0, 1200.0, PLAYING)
        got, stop = [], asyncio.Event()
        task = asyncio.create_task(ad.watch(server, lambda d, n: got.append((d.id, n.state if n else "online")), 0.05, stop))
        await asyncio.sleep(0.2)
        stop.set()
        await task
        assert got[:2] == [("home-server", "online"), ("home-server--living-room-tv", PLAYING)]
        data.set_secret("jellyfin:home-server", "wrong")
        with pytest.raises(AdapterError, match="refused"):
            await ad.sessions(server)
    finally:
        srv.close()


def test_jellyfin_webhook_payloads():
    ad = JellyfinAdapter(None)
    server = dev("jellyfin", "Home Server", identifier="srv1")
    base = dict(ServerId="srv1", DeviceName="Bedroom TV", DeviceId="x1", ClientName="Jellyfin Roku", ItemType="Episode",
                Name="Pilot", SeriesName="Show", SeasonNumber="1", EpisodeNumber="1", RunTimeTicks="12000000000",
                PlaybackPositionTicks="600000000", IsPaused="False")
    [(d, np)] = ad.handle_webhook(dict(base, NotificationType="PlaybackStart"), [server])
    assert d.id == "home-server--bedroom-tv" and (np.series, np.season, np.episode, np.position) == ("Show", 1, 1, 60.0)
    [(d, np)] = ad.handle_webhook(dict(base, NotificationType="PlaybackStop"), [server])
    assert np.state == IDLE
    assert ad.handle_webhook(dict(base, NotificationType="ItemAdded"), [server]) == []


# ------------------------------------------------------------------ Plex
PLEX_SESSIONS = {"MediaContainer": {"size": 1, "Metadata": [
    {"type": "episode", "title": "Career Day", "grandparentTitle": "Abbott Elementary", "parentIndex": 3, "index": 4,
     "viewOffset": 90000, "duration": 1320000, "User": {"title": "someone"},
     "Player": {"title": "Living Room Shield", "product": "Plex for Android (TV)", "state": "paused",
                "machineIdentifier": "m1"}}]}}


async def test_plex_pair_without_token_then_sessions(data):
    srv = fake_json_server({"/identity": {"MediaContainer": {"machineIdentifier": "pms1", "version": "1.41.0"}},
                            "/status/sessions": PLEX_SESSIONS})
    try:
        ad = PlexAdapter(data)
        server = await ad.pair(dev("plex", "Plex", port=srv.port), None)    # local network allowed without auth
        assert server.identifier == "pms1" and server.os_version == "1.41.0"
        [(cdev, np)] = await ad.sessions(server)
        assert cdev.name == "Living Room Shield · Plex" and np.state == PAUSED
        assert (np.series, np.season, np.episode, np.position) == ("Abbott Elementary", 3, 4, 90.0)
    finally:
        srv.close()


async def test_plex_pair_asks_for_token_when_needed(data):
    srv = fake_json_server({"/identity": {"MediaContainer": {"machineIdentifier": "pms1"}},
                            "/status/sessions": PLEX_SESSIONS}, require={"X-Plex-Token": "tok"})
    try:
        async def prompt(msg, need_input=True):
            return "tok"
        server = await PlexAdapter(data).pair(dev("plex", "Plex", port=srv.port), prompt)
        assert data.secret("plex:plex") == "tok" and server.identifier == "pms1"
    finally:
        srv.close()


def test_plex_webhook():
    ad = PlexAdapter(None)
    server = dev("plex", "Plex", identifier="pms1")
    payload = {"event": "media.resume", "Server": {"uuid": "pms1"}, "Player": {"title": "Bedroom", "uuid": "p2"},
               "Metadata": {"type": "movie", "title": "A Film", "viewOffset": 1000, "duration": 6000000}}
    [(d, np)] = ad.handle_webhook(payload, [server])
    assert d.id == "plex--bedroom" and np.title == "A Film" and np.state == PLAYING and np.duration == 6000.0
    assert ad.handle_webhook(dict(payload, event="library.new"), [server]) == []
