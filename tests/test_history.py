import time

from conftest import write_events
from strivo.core import Core
from strivo.history import norm, sessions_from_events


def ts(minutes_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - minutes_ago * 60))


def test_norm_per_app_rules():
    hulu = norm(dict(app="Hulu", title="Abbott Elementary | S3 E4 - Career Day", artist=""))
    assert (hulu["kind"], hulu["series"], hulu["season"], hulu["ep"], hulu["ept"]) == \
        ("episode", "Abbott Elementary", 3, 4, "Career Day")
    mx = norm(dict(app="HBO Max", title="The Long Night", artist="Some Drama"))
    assert (mx["kind"], mx["series"], mx["ept"]) == ("episode", "Some Drama", "The Long Night")
    pv = norm(dict(app="Prime Video", title="Show", artist="Season 2, Ep. 5 Pilot"))
    assert (pv["season"], pv["ep"]) == (2, 5)
    live = norm(dict(app="YouTube TV", title="Evening News", artist="NBC"))
    assert (live["kind"], live["ch"]) == ("live", "NBC")
    assert norm(dict(app="Netflix", title=None)) is None


def test_norm_trusts_explicit_episode_fields():
    """Plex / Jellyfin / some Apple TV apps send series + season + episode directly."""
    n = norm(dict(app="Jellyfin", title="Episode 2", series="Strivo Test Show", season=1, episode=2))
    assert (n["kind"], n["series"], n["season"], n["ep"], n["ept"]) == ("episode", "Strivo Test Show", 1, 2, "Episode 2")


def test_norm_generic_se_patterns():
    n = norm(dict(app="Some App", title="Great Show S02E05 The Heist"))
    assert (n["kind"], n["series"], n["season"], n["ep"]) == ("episode", "Great Show", 2, 5)
    n = norm(dict(app="Other", title="Great Show", artist="S2:E6 Aftermath"))
    assert (n["season"], n["ep"], n["ept"]) == (2, 6, "Aftermath")


def test_sessions_join_and_cap():
    ev = [dict(kind="play", dev="lr", app="Hulu", state="Playing", title="X | S1 E1 - A", ts=ts(60), pos=0, total=1800),
          dict(kind="play", dev="lr", app="Hulu", state="Paused", title="X | S1 E1 - A", ts=ts(50), pos=600, total=1800),
          dict(kind="play", dev="lr", app="Hulu", state="Playing", title="X | S1 E1 - A", ts=ts(45), pos=600, total=1800),
          dict(kind="play", dev="lr", app="Hulu", state="Idle", title=None, ts=ts(20))]
    sessions, last_state, _ = sessions_from_events(ev)
    assert len(sessions) == 1                       # pause + resume within 20 min is one session
    s = sessions[0]
    assert s["sec"] == 600 + (1800 - 600) + 60      # 10 min, then capped at the remaining runtime + 60 s
    assert last_state == {}


def test_build_queue_movies_daily(data, tmdb):
    data.set_secret("tmdb_key", "test-key")
    write_events(data, [
        dict(dev="lr", app="Jellyfin", title="Episode 1", series="Strivo Test Show", season=1, episode=1, ts=ts(300),
             pos=0, total=1500),
        dict(dev="lr", app="Jellyfin", title="Episode 1", series="Strivo Test Show", season=1, episode=1, ts=ts(276),
             state="Idle", pos=1490, total=1500),
        dict(dev="lr", app="Jellyfin", title="Episode 2", series="Strivo Test Show", season=1, episode=2, ts=ts(270),
             pos=0, total=1500),
        dict(dev="lr", app="Jellyfin", title="Episode 2", series="Strivo Test Show", season=1, episode=2, ts=ts(260),
             state="Paused", pos=600, total=1500),
        dict(dev="br", app="Netflix", title="A Long Film", ts=ts(200), pos=0, total=7200),
        dict(dev="br", app="Netflix", state="Idle", ts=ts(80)),
        dict(dev="lr", app="YouTube TV", title="Evening Report", artist="CBS", ts=ts(30)),
        dict(dev="lr", app="YouTube TV", state="Idle", ts=ts(10)),
    ])
    core = Core(data, tmdb)
    core.show("Strivo Test Show")          # warm the cache so unwatched counts are deterministic
    d = core.build()
    names = [s["series"] for s in d["series"]]
    assert names == ["Strivo Test Show"]
    sts = d["series"][0]
    assert [e["ep"] for e in sts["episodes"]] == [1, 2]
    assert sts["episodes"][0]["done"] is True       # watched to the end
    assert sts["latest"]["ep"] == 2 and not sts["latest"]["done"]
    assert sts["unwatched"] == 3                    # TMDB lists 5 aired episodes; you've reached 2
    assert [m["title"] for m in d["movies"]] == ["A Long Film"]
    assert d["daily"][0]["series"] == "Evening Report"
    assert d["sessions"][0]["dev"] == "lr"          # unknown ids fall back to the id
    assert set(d["hours"]) == {"Jellyfin", "Netflix", "YouTube TV"}


def test_remove_and_restore(data, tmdb):
    write_events(data, [dict(dev="lr", app="Hulu", title="Show | S1 E1 - A", ts=ts(100), pos=0, total=1800),
                        dict(dev="lr", app="Hulu", state="Idle", ts=ts(70))])
    core = Core(data, tmdb)
    data.write("dismissed.json", {"series:Show": time.time() - 60 * 60})   # removed an hour ago
    d = core.build()
    assert d["series"] == [] and d["removed"][0]["name"] == "Show"
    # watching it again for more than restore_min puts it back
    write_events(data, [dict(dev="lr", app="Hulu", title="Show | S1 E2 - B", ts=ts(30), pos=0, total=1800),
                        dict(dev="lr", app="Hulu", state="Idle", ts=ts(15))])
    assert [s["series"] for s in core.build()["series"]] == ["Show"]


def test_device_names_come_from_config(data, tmdb):
    data.update_config(lambda c: c.__setitem__("devices", [dict(id="lr", kind="appletv", name="Family Room")]))
    write_events(data, [dict(dev="lr", app="Hulu", title="Show | S1 E1 - A", ts=ts(100), pos=0, total=1800),
                        dict(dev="lr", app="Hulu", state="Idle", ts=ts(70))])
    assert Core(data, tmdb).build()["sessions"][0]["dev"] == "Family Room"
