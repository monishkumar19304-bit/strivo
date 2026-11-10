"""The whole app over HTTP with fake boxes and a fake TMDB: wizard, pairing, Play on TV, webhooks, privacy."""
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from fakes import FakeAdapter, FakeTMDB, dev
from strivo.adapters.base import NowPlaying, PLAYING
from strivo.adapters.jellyfin import JellyfinAdapter
from strivo.adapters.plex import PlexAdapter
from strivo.server import App, make_handler


class Client:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            body = r.read()
            return json.loads(body) if r.headers.get("Content-Type", "").startswith("application/json") else body

    def post(self, path, obj=None, raw=None, ctype="application/json"):
        data = raw if raw is not None else json.dumps(obj or {}).encode()
        req = urllib.request.Request(self.base + path, data=data, method="POST", headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())


@pytest.fixture
def app(tmp_path):
    fake = FakeAdapter(found=[dev("fake", "Living Room", host="10.0.0.5", model="Fake Box 4K")],
                       script=[NowPlaying(state=PLAYING, app="Hulu", title="Abbott Elementary | S3 E4 - Career Day",
                                          position=10, duration=1320)])
    fake.kind = "appletv"   # behave like a TV box for Play on TV
    tmdb = FakeTMDB()
    a = App(tmp_path / "data", transport=tmdb, adapters={"appletv": fake, "jellyfin": JellyfinAdapter(None),
                                                         "plex": PlexAdapter(None)}, start_background=False)
    a.collector.adapters["jellyfin"].data = a.data
    a.collector.adapters["plex"].data = a.data
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(a))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    a.runtime.call(a.collector.start(), 5)
    yield a, Client(httpd.server_address[1]), fake, tmdb
    httpd.shutdown()
    a.close()


def wait(pred, t=5):
    end = time.time() + t
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_static_and_empty_state(app):
    a, c, *_ = app
    html = c.get("/")
    assert b"<title>Strivo</title>" in html and b"NiVo" not in html and b"data-theme=retro" not in html
    st = c.get("/api/setup")
    assert st["setup_done"] is False and st["tmdb_key_set"] is False and st["devices"] == []
    from strivo import __version__
    assert st["version"] == __version__ and st["rules_version"] == a.rules.version     # shown in Settings › About
    assert b"<h3>About</h3>" in html and b"Screen: " not in html
    assert b'id="splash"' in html and b"prefers-reduced-motion" in html             # launch animation, motion-safe
    assert c.get("/api/data")["series"] == []
    assert json.loads(c.get("/manifest.webmanifest"))["name"] == "Strivo"


def test_wizard_end_to_end(app):
    a, c, fake, tmdb = app
    found = c.post("/api/setup/discover", {"timeout": 1})[1]["found"]
    assert found[0]["name"] == "Living Room" and found[0]["added"] is False
    code, p = c.post("/api/setup/pair", {"kind": "appletv", "name": "Living Room", "host": "10.0.0.5"})
    assert code == 200
    assert wait(lambda: c.get(f"/api/setup/pair?id={p['id']}")["state"] == "need_input")
    assert "code" in c.get(f"/api/setup/pair?id={p['id']}")["message"]
    c.post("/api/setup/pair/input", {"id": p["id"], "value": "1234"})
    assert wait(lambda: c.get(f"/api/setup/pair?id={p['id']}")["state"] == "done")
    st = c.get("/api/setup")
    assert [d["name"] for d in st["devices"]] == ["Living Room"] and st["devices"][0]["model"] == "Fake Box 4K"
    # the collector picked the new box up and logged what it's playing
    assert wait(lambda: os.path.exists(a.data.file("events.jsonl")) and "Career Day" in open(a.data.file("events.jsonl")).read())
    # TMDB key: a bad one is refused, a good one is stored privately and never served back
    assert c.post("/api/setup/tmdb", {"key": "bad"})[0] == 400
    assert c.post("/api/setup/tmdb", {"key": "good-test-key"})[0] == 200
    assert oct(os.stat(a.data.file("secrets.json")).st_mode)[-3:] == "600"
    for path in ("/api/setup", "/api/settings", "/api/data", "/api/health"):
        assert "good-test-key" not in json.dumps(c.get(path))
    c.post("/api/setup/prefs", {"country": "gb", "my_services": ["Netflix", "Not A Service"], "default_tv": "living-room"})
    c.post("/api/setup/device", {"id": "living-room", "name": "Family Room"})
    c.post("/api/setup/done")
    st = c.get("/api/setup")
    assert st["setup_done"] and st["country"] == "GB" and st["services"] == ["Netflix"] and st["default_tv"] == "living-room"
    assert st["devices"][0]["name"] == "Family Room"


def test_wrong_pin_reports_error(app):
    a, c, fake, _ = app
    p = c.post("/api/setup/pair", {"kind": "appletv", "name": "Den", "host": "10.0.0.6"})[1]
    assert wait(lambda: c.get(f"/api/setup/pair?id={p['id']}")["state"] == "need_input")
    c.post("/api/setup/pair/input", {"id": p["id"], "value": "0000"})
    assert wait(lambda: c.get(f"/api/setup/pair?id={p['id']}")["state"] == "error")
    assert "wrong code" in c.get(f"/api/setup/pair?id={p['id']}")["error"]
    assert c.get("/api/setup")["devices"] == []


def test_play_on_tv(app):
    a, c, fake, _ = app
    a.devices.save_device(dev("appletv", "Living Room", host="10.0.0.5"))
    tvs = c.get("/api/tvs")["devices"]
    assert tvs == [dict(id="living-room", name="Living Room", kind="appletv", online=True, power="on", app="Hulu")]
    assert c.post("/api/tv/play", {"dev": "living-room", "provider": "Hulu"}) == (200, {"result": "ok", "what": "Hulu"})
    assert c.post("/api/tv/play", {"dev": "living-room", "trailer": "abcdefghijk"})[0] == 200
    assert fake.launched == [("living-room", "com.hulu.plus", None),
                             ("living-room", "com.google.ios.youtube", "youtube://watch/abcdefghijk")]
    assert c.post("/api/tv/play", {"dev": "living-room", "provider": "Hulu + Live TV"})[0] == 200   # opens Hulu
    assert fake.launched[-1] == ("living-room", "com.hulu.plus", None)
    assert c.post("/api/tv/play", {"dev": "living-room", "provider": "Nope"})[0] == 502
    assert c.post("/api/tv/play", {"dev": "ghost", "provider": "Hulu"})[0] == 502


def test_webhooks_jellyfin_and_plex(app):
    a, c, *_ = app
    a.devices.save_device(dev("jellyfin", "Home Server", identifier="srv1"))
    a.devices.save_device(dev("plex", "Plex", identifier="pms1"))
    code, r = c.post("/api/webhook/jellyfin", dict(NotificationType="PlaybackStart", ServerId="srv1",
                                                   DeviceName="Bedroom TV", ItemType="Episode", Name="Pilot",
                                                   SeriesName="Show", SeasonNumber="1", EpisodeNumber="1",
                                                   RunTimeTicks="12000000000", PlaybackPositionTicks="0", IsPaused="False"))
    assert code == 200 and r["n"] == 1
    payload = json.dumps({"event": "media.play", "Server": {"uuid": "pms1"}, "Player": {"title": "Shield", "uuid": "p"},
                          "Metadata": {"type": "movie", "title": "A Film", "duration": 6000000}})
    body = ("--XyZ\r\nContent-Disposition: form-data; name=\"payload\"\r\nContent-Type: application/json\r\n\r\n" + payload +
            "\r\n--XyZ\r\nContent-Disposition: form-data; name=\"thumb\"; filename=\"t.jpg\"\r\n\r\n\xff\xd8\r\n--XyZ--\r\n")
    code, r = c.post("/api/webhook/plex", raw=body.encode("latin-1"), ctype="multipart/form-data; boundary=XyZ")
    assert code == 200 and r["n"] == 1
    rows = [json.loads(x) for x in open(a.data.file("events.jsonl"))]
    assert {(x["dev"], x["title"]) for x in rows if x["kind"] == "play"} >= {("home-server--bedroom-tv", "Pilot"),
                                                                             ("plex--shield", "A Film")}
    names = {h["id"]: h["name"] for h in c.get("/api/health")["devices"]}
    assert names["home-server--bedroom-tv"] == "Bedroom TV · Home Server"


def test_lists_and_settings(app):
    a, c, *_ = app
    a.data.set_secret("tmdb_key", "k")
    c.post("/api/wish", {"id": 201, "type": "movie", "title": "Test Movie"})
    w = c.get("/api/wishlist")
    assert w[0]["title"] == "Test Movie" and w[0]["providers"][0]["name"] == "Netflix"
    c.post("/api/queue/add", {"id": 101, "title": "Strivo Test Show"})
    assert c.get("/api/data")["queued"][0]["title"] == "Strivo Test Show"
    s = c.post("/api/settings", {"queue_days": 999, "notify_url": "javascript:alert(1)", "country": "123"})[1]
    assert s["queue_days"] == 60 and s["notify_url"] == "" and s["country"] == "US"
    assert c.get("/api/search?q=Test%20Movie")[0]["id"] == 201
    ex = c.get("/api/suggest/extras?id=201")
    assert ex["trailer"] == "abcdefghijk"
    assert c.post("/api/suggest/feedback", {"id": 201, "reason": "liked", "title": "Test Movie"})[0] == 200
    assert c.post("/api/nope", {})[0] == 400


def test_picks_build_with_fake_tmdb(app):
    from strivo import picks
    a, c, *_ = app
    a.data.set_secret("tmdb_key", "k")
    a.core.settings_save({"my_services": ["Netflix"]})
    picks.build_all(a.core)
    assert c.get("/api/suggest")["items"][0]["title"] == "Picked Movie"
    assert c.get("/api/shows")["items"][0]["title"] == "Picked Show"
    assert c.get("/api/discover?cat=docs")["items"]


def test_sports_api(app, tmp_path):
    from test_sports import FakeProvider, fixture
    a, c, *_ = app
    a.sports.provider = FakeProvider({"basketball/nba": fixture("espn_nba_day.json")})
    a.data.update_config(lambda cfg: cfg.update(services=["YouTube TV"]))
    st, p = c.post("/api/sports/prefs", {"leagues": ["nba"]})
    assert st == 200 and p["leagues"] == ["nba"]
    r = c.get("/api/sports")
    assert r["games"] and all(g["league"] == "nba" for g in r["games"])
    assert any("YouTube TV" in g["mine"] for g in r["games"])          # NBC -> YouTube TV
    assert c.post("/api/sports/prefs", {"follow": {"league": "nba", "id": "18", "name": "New York Knicks"}})[1]["teams"]
    assert c.get("/api/sports?leagues=nba")["teams"][0]["name"] == "New York Knicks"
