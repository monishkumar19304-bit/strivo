"""Google TV / Android TV / Fire TV adapter: parsers on real captured output, and a fake ADB connection."""
import os

import pytest

from fakes import dev
from strivo.adapters import android as amod
from strivo.adapters.android import (SEP, AndroidAdapter, parse_description, parse_foreground, parse_media_sessions,
                                     poll_to_np)
from strivo.adapters.base import OFF, PAUSED, PLAYING, AdapterError
from strivo.history import norm

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
REAL = open(os.path.join(FIX, "android14_vlc_playing.txt")).read()   # captured from the Google TV 14 emulator

# Android 9-11 style (Fire OS 7/8, older Android TV): numeric state, no PLAYING(3)
OLD = """  Sessions Stack - have 2 sessions:
    Hulu com.hulu.plus/Hulu (userId=0)
      ownerPid=2001, ownerUid=10051, userId=0
      package=com.hulu.plus
      active=true
      flags=3
      state=PlaybackState {state=2, position=845000, buffered position=0, speed=0.0, updated=99000, actions=823, custom actions=[], active item id=-1, error=null}
      metadata: size=6, description=Abbott Elementary | S3 E4 - Career Day, Hulu, null
    MediaSessionCompat com.netflix.ninja/MediaSessionCompat (userId=0)
      package=com.netflix.ninja
      active=false
      state=PlaybackState {state=1, position=0, buffered position=0, speed=0.0, updated=1000, actions=0, custom actions=[], active item id=-1, error=null}
      metadata: null
"""


def test_parse_real_android14_capture():
    s = parse_media_sessions(REAL, uptime_ms=140250)
    vlc = s[0]
    assert vlc["package"] == "org.videolan.vlc" and vlc["state"] == 3 and vlc["active"]
    assert vlc["title"] == "Strivo Test Show - S01E02 - Episode 2"
    assert vlc["subtitle"] is None                     # "Unknown Album" is dropped
    assert vlc["position"] == 20055                    # position advanced from `updated` to now
    assert {x["package"] for x in s} == {"org.videolan.vlc", "com.google.android.bluetooth"}


def test_parse_old_format_and_pick_paused():
    s = parse_media_sessions(OLD)
    assert [(x["package"], x["state"]) for x in s] == [("com.hulu.plus", 2), ("com.netflix.ninja", 1)]
    np = poll_to_np(OLD + SEP + "  mResumedActivity: ActivityRecord{1 u0 com.hulu.plus/.MainActivity t3}" + SEP +
                    "mWakefulness=Awake")
    assert np.state == PAUSED and np.app == "Hulu" and np.position == 845.0
    n = norm(np.to_event())
    assert (n["series"], n["season"], n["ep"]) == ("Abbott Elementary", 3, 4)


def test_full_poll_real_capture_to_episode():
    out = REAL + SEP + "    topResumedActivity=ActivityRecord{64c4cef u0 org.videolan.vlc/.gui.video.VideoPlayerActivity t6}" \
        + SEP + "  mWakefulness=Awake" + SEP + "140.25 205.60"
    np = poll_to_np(out)
    assert np.state == PLAYING and np.app == "VLC" and np.app_id == "org.videolan.vlc"
    n = norm(np.to_event())
    assert (n["kind"], n["series"], n["season"], n["ep"], n["ept"]) == ("episode", "Strivo Test Show", 1, 2, "Episode 2")


def test_screen_off_and_idle():
    assert poll_to_np(REAL + SEP + "" + SEP + "mWakefulness=Asleep").state == OFF
    np = poll_to_np("" + SEP + "topResumedActivity=ActivityRecord{1 u0 com.netflix.ninja/.MainActivity t1}" + SEP +
                    "mWakefulness=Awake")
    assert np.state == "Idle" and np.app == "Netflix"   # app only: tracked as nothing, shown as the app


def test_parse_description_commas():
    assert parse_description("Law, Order, null") == ("Law", "Order", None)
    assert parse_description("Crime, Punishment, and More, Some Show, null") == ("Crime, Punishment, and More", "Some Show", None)
    assert parse_description("Title, null, null") == ("Title", None, None)
    assert parse_foreground("  mFocusedApp=AppWindowToken{x token=Token{y ActivityRecord{z u0 com.a.b/.X t9}}}") == "com.a.b"


class FakeConn:
    def __init__(self, outputs):
        self.available = True
        self.outputs = outputs
        self.cmds = []

    async def connect(self, **kw):
        self.kw = kw

    async def shell(self, cmd, **kw):
        self.cmds.append(cmd)
        for k, v in self.outputs.items():
            if cmd.startswith(k):
                return v
        return ""

    async def close(self):
        self.available = False


@pytest.fixture
def fake_adb(monkeypatch):
    holder = {}
    outputs = {"getprop": "Amazon\nAFTKA\n9\nG4N0000000000001\ntv\n", "dumpsys media_session": OLD + SEP +
               "mResumedActivity: ActivityRecord{1 u0 com.hulu.plus/.MainActivity t3}" + SEP + "mWakefulness=Awake",
               "monkey -p com.hulu.plus": "Events injected: 1", "monkey -p": "No activities found to run, monkey aborted."}

    class Fake(FakeConn):
        def __init__(self, host, port, default_transport_timeout_s=None):
            super().__init__(outputs)
            holder["conn"], holder["addr"] = self, (host, port)

    import adb_shell.adb_device_async as m
    monkeypatch.setattr(m, "AdbDeviceTcpAsync", Fake)
    return holder


async def test_pair_reads_model_and_detects_fire_tv(data, fake_adb):
    msgs = []

    async def prompt(msg, need_input=True):
        msgs.append((msg, need_input))
        return ""
    d = await AndroidAdapter(data).pair(dev("android", "Den", host="10.0.0.9", port=5555), prompt)
    assert msgs and msgs[0][1] is False and "Allow" in msgs[0][0]
    assert d.platform == "firetv" and d.model == "Amazon AFTKA" and d.os_version.startswith("Fire OS")
    assert fake_adb["conn"].kw["auth_timeout_s"] == 90         # waits for someone to press Allow
    assert os.path.exists(data.file("adbkey")) and oct(os.stat(data.file("adbkey")).st_mode)[-3:] == "600"


async def test_now_playing_and_launch(data, fake_adb):
    ad = AndroidAdapter(data)
    d = dev("android", host="10.0.0.9", port=5555)
    np = await ad.now_playing(d)
    assert np.app == "Hulu" and np.state == PAUSED
    await ad.launch(d, "com.hulu.plus")
    assert any(c.startswith("monkey -p com.hulu.plus -c android.intent.category.LEANBACK_LAUNCHER")
               for c in fake_adb["conn"].cmds)
    await ad.launch(d, "com.google.android.youtube.tv", "https://www.youtube.com/watch?v=abcdefghijk")
    assert "am start -a android.intent.action.VIEW -d 'https://www.youtube.com/watch?v=abcdefghijk' " \
           "-p com.google.android.youtube.tv" in fake_adb["conn"].cmds
    with pytest.raises(AdapterError, match="isn't installed"):
        await ad.launch(d, "com.example.missing")
    with pytest.raises(AdapterError):
        await ad.launch(d, "com.x; reboot")                  # no shell injection through the package name
    with pytest.raises(AdapterError):
        await ad.launch(d, "com.x", "https://x.test/'; reboot")


def test_platform_ids_cover_common_services():
    from strivo.apps import SERVICES
    for name in ("Netflix", "Hulu", "YouTube", "Disney+", "Prime Video"):
        assert SERVICES[name]["android"] and SERVICES[name]["firetv"]
    assert amod.platform_key(dev("android", platform="firetv")) == "firetv"


def test_parse_real_android12_capture():
    """Captured from the Android TV 12 emulator: the numeric `state=3` format (also Fire OS 7/8)."""
    t = open(os.path.join(FIX, "android12_vlc_playing.txt")).read()
    vlc = parse_media_sessions(t)[0]
    assert (vlc["package"], vlc["state"], vlc["title"]) == ("org.videolan.vlc", 3, "Strivo Test Show - S01E03 - Episode 3")
    n = norm(poll_to_np(t + SEP + "mResumedActivity: ActivityRecord{1 u0 org.videolan.vlc/.X t3}" + SEP +
                        "mWakefulness=Awake").to_event())
    assert (n["series"], n["season"], n["ep"]) == ("Strivo Test Show", 1, 3)
