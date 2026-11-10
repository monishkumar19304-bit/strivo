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


def _days(n):
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - n * 86400))


def test_removed_show_returns_for_a_new_season(data, tmdb):
    """Removed a year ago; season 2 premieres; it comes back with a badge. Removing it again hides it until season 3."""
    data.set_secret("tmdb_key", "k")
    tmdb.shows["Strivo Test Show"]["seasons"] = [dict(season_number=1), dict(season_number=2)]
    tmdb.episodes[(101, 2)] = [dict(episode_number=1, name="Back", air_date=_days(10)),
                               dict(episode_number=2, name="Again", air_date=_days(3)),
                               dict(episode_number=3, name="Later", air_date=_days(-4))]   # not aired yet
    year = 365 * 24 * 60
    write_events(data, [dict(dev="lr", app="Hulu", title="Strivo Test Show | S1 E5 - Episode 5", ts=ts(year),
                             pos=0, total=1800),
                        dict(dev="lr", app="Hulu", state="Idle", ts=ts(year - 40))])
    core = Core(data, tmdb)
    core.show("Strivo Test Show", force=True)
    data.write("dismissed.json", {"series:Strivo Test Show": time.time() - 200 * 86400})
    d = core.build()
    sr = d["series"][0]
    assert sr["series"] == "Strivo Test Show" and sr["new_season"] == 2 and sr["unwatched"] == 2
    assert d["removed"] == []
    core.set_dismissed("series:Strivo Test Show", True)        # removed again: stays away this season
    d = core.build()
    assert d["series"] == [] and d["removed"][0]["name"] == "Strivo Test Show"
    assert core.season_since("Strivo Test Show", time.time() - 5 * 86400) is None   # season 2 started before that


def test_old_history_is_paged_not_sent_every_refresh(data, tmdb):
    rows = []
    for day in range(0, 200, 5):                       # one 30-minute show every 5 days for 200 days
        rows += [dict(dev="lr", app="Hulu", title=f"Show {day} | S1 E1 - A", ts=ts(day * 1440 + 60), pos=0,
                      total=3600),
                 dict(dev="lr", app="Hulu", state="Idle", ts=ts(day * 1440 + 30))]
    write_events(data, rows)
    core = Core(data, tmdb)
    d = core.build()
    assert len(d["sessions"]) == 18 and d["sessions_more"] == 22          # 0..85 days in, 90..195 days paged
    p1 = core.history_page(d["sessions"][-1]["t0"], limit=15)
    assert len(p1["sessions"]) == 15 and p1["more"] == 7
    p2 = core.history_page(p1["sessions"][-1]["t0"], limit=15)
    assert len(p2["sessions"]) == 7 and p2["more"] == 0
    seen = [x["series"] for x in d["sessions"] + p1["sessions"] + p2["sessions"]]
    assert len(seen) == len(set(seen)) == 40


def test_build_is_reused_until_something_changes(data, tmdb):
    write_events(data, [dict(dev="lr", app="Hulu", title="Show | S1 E1 - A", ts=ts(100), pos=0, total=1800),
                        dict(dev="lr", app="Hulu", state="Idle", ts=ts(70))])
    core = Core(data, tmdb)
    calls = []
    real = core._build_full
    core._build_full = lambda now: calls.append(now) or real(now)
    a = core.build()
    b = core.build()
    assert len(calls) == 1 and a == b
    b["series"].clear()                                  # callers can't damage the reused copy
    assert core.build()["series"]
    core.set_dismissed("series:Show", True)              # a change on disk rebuilds
    assert core.build()["series"] == [] and len(calls) == 2
    write_events(data, [dict(dev="lr", app="Hulu", title="Other | S1 E1 - A", ts=ts(20), pos=0, total=1800),
                        dict(dev="lr", app="Hulu", state="Idle", ts=ts(5))])
    assert [s["series"] for s in core.build()["series"]] == ["Other"] and len(calls) == 3


def _canon(sessions):
    return sorted((x["dev"], x["key"], round(x["t0"], 3), round(x["t1"], 3), round(x["sec"], 3), x["pos"], x["total"])
                  for x in sessions)


def test_incremental_builder_matches_reference():
    """SessionBuilder fed one event at a time gives exactly what sessions_from_events gives for the whole log."""
    import random
    from datetime import datetime, timedelta
    from strivo.history import SessionBuilder
    rnd = random.Random(7)
    base = datetime(2025, 3, 1, 19, 0, 0)
    titles = ["Abbott Elementary | S3 E4 - Career Day", "Abbott Elementary | S3 E5 - Next", "Evening News",
              "Some Movie", None]
    for trial in range(30):
        ev, clock = [], {d: base for d in ("lr", "st", "bd")}
        for _ in range(rnd.randint(1, 120)):
            dev = rnd.choice(list(clock))
            clock[dev] += timedelta(seconds=rnd.choice([0, 5, 60, 300, 900, 1500, 3600, 5 * 3600]))
            t = rnd.choice(titles)
            app = "YouTube TV" if t == "Evening News" else rnd.choice(["Hulu", "Netflix"])
            ev.append(dict(kind=rnd.choice(["play"] * 9 + ["power"]), dev=dev, ts=clock[dev].isoformat(), app=app,
                           state=rnd.choice(["Playing", "Playing", "Paused", "Idle"]), title=t,
                           artist="NBC" if t == "Evening News" else None,
                           pos=rnd.choice([None, rnd.randint(0, 3000)]), total=rnd.choice([None, 1800, 7200])))
        now = max(clock.values()).timestamp() + rnd.choice([0, 600, 6 * 3600])
        ref, ref_last, _ = sessions_from_events([dict(e) for e in ev], now)
        sb = SessionBuilder()
        for e in ev:                       # arrival order: per device in time order, devices interleaved
            assert sb.feed(dict(e))
        got, got_last = sb.result(now)
        assert _canon(got) == _canon(ref), trial
        assert {d: (v["app"], v["n"]["series"], v["pos"]) for d, v in got_last.items()} == \
               {d: (v["app"], v["n"]["series"], v["pos"]) for d, v in ref_last.items()}, trial
        assert _canon(sb.result(now)[0]) == _canon(got)            # result() doesn't change the builder


def test_builder_refuses_out_of_order():
    from strivo.history import SessionBuilder
    sb = SessionBuilder()
    assert sb.feed(dict(kind="play", dev="lr", ts="2025-01-02T10:00:00", state="Playing", title="A"))
    assert not sb.feed(dict(kind="play", dev="lr", ts="2025-01-01T10:00:00", state="Playing", title="B"))
    assert sb.feed(dict(kind="play", dev="other", ts="2025-01-01T10:00:00", state="Playing", title="C"))
