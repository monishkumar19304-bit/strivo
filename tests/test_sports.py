"""Sports: parsing the schedule feed (real captured responses), where-to-watch, caching, prefs, the API."""
import copy
import datetime as dt
import json
import os

import pytest

from strivo import rules
from strivo.sports import Sports
from strivo.sports.espn import ESPN, parse_event

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    with open(os.path.join(FIX, name)) as f:
        return json.load(f)


def ts(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


class FakeProvider:
    """Serves captured scoreboards by league path; any day returns the same events."""

    def __init__(self, by_path, fail=()):
        self.by_path, self.fail, self.calls = by_path, set(fail), []

    def day(self, path, yyyymmdd):
        self.calls.append((path, yyyymmdd))
        if path in self.fail:
            raise OSError("network down")
        return ESPN(fetch=lambda url: copy.deepcopy(self.by_path.get(path, {"events": []}))).day(path, yyyymmdd)


@pytest.fixture(autouse=True)
def bundled_rules():
    rules.use(rules.Rules())


def test_parse_real_nfl_scoreboard():
    games = [parse_event(e) for e in fixture("espn_nfl_day.json")["events"]]
    g = games[0]
    assert g["short"] and g["state"] == "post"
    assert [t["home"] for t in g["teams"]] == [False, True]          # away first
    assert g["teams"][0]["name"] == "Jacksonville Jaguars" and g["teams"][1]["logo"].startswith("https://")
    assert g["channels"][0]["name"] == "Prime Video"
    assert any(t["winner"] for t in g["teams"])


def test_parse_golf_is_an_event_with_a_leaderboard():
    g = parse_event(fixture("espn_golf_day.json")["events"][0])
    assert g["teams"] == [] and len(g["leaders"]) >= 3
    assert {c["name"] for c in g["channels"]} >= {"ESPN+", "Golf Chnl"}


def test_where_to_watch_maps_channels_to_my_services(data):
    s = Sports(data)
    w = s.where([{"name": "FOX", "type": "TV", "market": "National"}], ["YouTube TV", "Netflix"])
    assert w["mine"] == ["YouTube TV"] and w["where"][0]["channel"] == "Fox"
    w = s.where([{"name": "Prime Video", "type": "Streaming", "market": "National"}], ["Prime Video"])
    assert w["mine"] == ["Prime Video"]
    w = s.where([{"name": "NFL Net", "type": "TV", "market": "National"}], [])
    assert w["mine"] == [] and "NFL+" in w["where"][0]["services"]
    # regional channels and team apps are flagged as local, radio is dropped, mine sorts first
    w = s.where([{"name": "MyMi", "type": "TV", "market": "Away"}, {"name": "ERADM", "type": "Radio", "market": "National"},
                 {"name": "ESPN+", "type": "Streaming", "market": "National"}], ["ESPN"])
    assert [x["channel"] for x in w["where"]] == ["ESPN+", "MyMi"] and w["where"][1]["local"]


def test_games_sorted_filtered_cached_and_errors_keep_old_data(data):
    data.update_config(lambda c: c.update(services=["Prime Video", "YouTube TV"], sports_leagues=["nfl", "nba"]))
    nfl, nba = fixture("espn_nfl_day.json"), fixture("espn_nba_day.json")
    # one NFL game is live, the others are long over
    nfl["events"][1]["competitions"][0]["status"]["type"].update(state="in", shortDetail="Q3 5:12")
    now = ts("2026-11-08T16:00Z")
    prov = FakeProvider({"football/nfl": nfl, "basketball/nba": nba})
    s = Sports(data, prov, clock=lambda: now)
    r = s.games()
    names = [g["name"] for g in r["games"]]
    assert names[0] == "Cincinnati Bengals at Atlanta Falcons"              # live first
    assert "Jacksonville Jaguars at Baltimore Ravens" not in names            # a final from days ago is gone
    assert r["games"][0]["league"] == "nfl" and r["games"][0]["league_name"] == "NFL"
    assert {lg["id"] for lg in r["leagues"] if lg["on"]} == {"nfl", "nba"}
    n = len(prov.calls)
    s.games()
    assert len(prov.calls) == n                                               # served from cache
    # the feed goes down: keep what we had and say so
    prov.fail.add("football/nfl")
    s.cache = {k: (0, v) for k, (_, v) in s.cache.items()}                    # everything expired
    r2 = s.games()
    assert any(g["league"] == "nfl" for g in r2["games"]) and r2["errors"] == ["Couldn't reach the NFL schedule"]


def test_prefs_defaults_follow_unfollow(data):
    s = Sports(data)
    assert set(s.prefs()["leagues"]) == {"nfl", "cfb", "nba", "mlb", "nhl", "pga"}
    assert s.save_prefs({"leagues": ["nba", "epl", "bogus"]})["leagues"] == ["nba", "epl"]
    t = dict(league="nfl", id="19", name="New York Giants", abbr="NYG", logo="https://x/nyg.png")
    s.save_prefs({"follow": t})
    s.save_prefs({"follow": t})
    assert [x["id"] for x in s.prefs()["teams"]] == ["19"]
    assert s.save_prefs({"follow": dict(t, league="nope", id="1")})["teams"] == s.prefs()["teams"]
    assert s.save_prefs({"unfollow": {"league": "nfl", "id": "19"}})["teams"] == []


def test_sports_rules_are_validated():
    r = rules.copy_of_bundled()
    rules.validate(json.dumps(r).encode())
    bad = copy.deepcopy(r)
    bad["sports"]["leagues"].append({"id": "x", "path": "../../etc", "name": "X"})
    with pytest.raises(rules.RulesError):
        rules.validate(json.dumps(bad).encode())
    bad = copy.deepcopy(r)
    bad["sports"]["networks"]["Fox"] = "YouTube TV"
    with pytest.raises(rules.RulesError):
        rules.validate(json.dumps(bad).encode())
    assert rules.current().play_as("Hulu + Live TV") == "Hulu" and rules.current().play_as("Peacock") == "Peacock"
    # every service a channel maps to can be picked in "My services"
    streaming = set(r["streaming"])
    assert {s for v in r["sports"]["networks"].values() for s in v} <= streaming
