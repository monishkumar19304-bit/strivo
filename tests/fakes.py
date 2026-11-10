"""Fake devices and services for tests. Nothing here touches the network beyond 127.0.0.1."""
from __future__ import annotations

import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from strivo.adapters.base import IDLE, Adapter, AdapterError, Device, NowPlaying, Status


# --------------------------------------------------------------------------- TMDB
class FakeTMDB:
    """Callable transport for strivo.tmdb.TMDB. Records every request; never sees a real key."""

    def __init__(self):
        self.calls = []
        self.shows = {
            "Strivo Test Show": dict(id=101, name="Strivo Test Show", genres=[{"id": 18, "name": "Drama"}],
                                     poster_path="/sts.jpg", overview="A show for tests.",
                                     seasons=[dict(season_number=1)]),
            "Late Night Test": dict(id=102, name="Late Night Test", genres=[{"id": 10767, "name": "Talk"}],
                                    poster_path="/lnt.jpg", overview="", seasons=[]),
        }
        self.episodes = {(101, 1): [dict(episode_number=i, name=f"Episode {i}", air_date=f"2024-01-0{i}")
                                    for i in range(1, 6)]}

    def __call__(self, url, headers):
        u = urllib.parse.urlparse(url)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        path = u.path.replace("/3", "", 1)
        self.calls.append((path, q, headers))
        if path == "/configuration":
            if q.get("api_key") == "bad" or headers.get("Authorization") == "Bearer bad":
                raise RuntimeError("401")
            return {"images": {}}
        if path == "/search/tv":
            s = self.shows.get(q.get("query"))
            return {"results": [dict(id=s["id"], name=s["name"], poster_path=s["poster_path"])] if s else []}
        if path in ("/search/multi", "/search/movie"):
            s = self.shows.get(q.get("query"))
            if s:
                return {"results": [dict(id=s["id"], name=s["name"], media_type="tv", poster_path=s["poster_path"])]}
            if q.get("query") == "Test Movie":
                return {"results": [dict(id=201, title="Test Movie", media_type="movie", poster_path="/tm.jpg",
                                         release_date="2023-05-01", overview="A movie.")]}
            return {"results": []}
        m = re.fullmatch(r"/tv/(\d+)/season/(\d+)", path)
        if m:
            return {"episodes": self.episodes.get((int(m.group(1)), int(m.group(2))), [])}
        m = re.fullmatch(r"/tv/(\d+)", path)
        if m:
            s = next((v for v in self.shows.values() if v["id"] == int(m.group(1))),
                     dict(id=int(m.group(1)), name="Picked Show", genres=[], seasons=[]))
            return dict(s, networks=[{"name": "NBC"}], number_of_seasons=1)
        if re.fullmatch(r"/(tv|movie)/\d+/watch/providers", path):
            return {"results": {"US": {"flatrate": [{"provider_name": "Netflix"}], "link": "https://example.test"}}}
        if re.fullmatch(r"/movie/\d+", path):
            return dict(id=201, title="Test Movie", genres=[], runtime=100,
                        videos={"results": [{"site": "YouTube", "type": "Trailer", "key": "abcdefghijk", "official": True}]},
                        reviews={"results": []}, external_ids={"imdb_id": "tt0000001"})
        if path.startswith("/genre/"):
            return {"genres": [{"id": 28, "name": "Action"}, {"id": 18, "name": "Drama"}]}
        if path.startswith("/watch/providers/"):
            return {"results": [{"provider_name": "Netflix", "provider_id": 8}]}
        if path.startswith("/discover/"):
            return {"results": [dict(id=301, title="Picked Movie", name="Picked Show", poster_path="/p.jpg",
                                     vote_average=8.1, vote_count=900, release_date="2025-01-01",
                                     first_air_date="2025-01-01", genre_ids=[18], media_type="movie")]}
        return {"results": []}


# --------------------------------------------------------------------------- adapters
class FakeAdapter(Adapter):
    """A scripted box: now_playing() returns the next item from `script`; launch() is recorded."""
    kind = "fake"
    label = "Fake box"

    def __init__(self, data=None, script=None, pin="1234", found=None):
        super().__init__(data)
        self.script = list(script or [])
        self.launched = []
        self.pin = pin
        self.found = found or []
        self.prompts = []

    async def discover(self, timeout=5):
        return list(self.found)

    async def pair(self, device, prompt):
        self.prompts.append("asked")
        got = await prompt(f"Type the code shown on {device.name}")
        if got != self.pin:
            raise AdapterError("wrong code")
        device.model = "Fake Box 4K"
        device.os_version = "1.0"
        return device

    async def now_playing(self, device):
        if not self.script:
            return NowPlaying(state=IDLE)
        return self.script.pop(0)

    async def launch(self, device, app, deep_link=None):
        self.launched.append((device.id, app, deep_link))

    async def status(self, device):
        return Status(online=True, power="on", app="Hulu")


# --------------------------------------------------------------------------- HTTP fakes
class _Server:
    def __init__(self, handler_cls):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self.port = self.httpd.server_address[1]
        self.t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.t.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def fake_roku_server(state: dict) -> _Server:
    """A fake Roku ECP endpoint. `state` is mutable: app, app_id, player_state, pos_ms, dur_ms, power, title, launched."""

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _xml(self, body):
            b = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            p = self.path.split("?")[0]
            if p == "/query/device-info":
                return self._xml(f"""<?xml version="1.0" encoding="UTF-8" ?>
<device-info><udn>x</udn><serial-number>X00000000001</serial-number><vendor-name>Roku</vendor-name>
<model-name>Roku Ultra</model-name><model-number>4800X</model-number><user-device-name>Den Roku</user-device-name>
<software-version>13.0.0</software-version><power-mode>{state.get('power', 'PowerOn')}</power-mode></device-info>""")
            if p == "/query/active-app":
                if state.get("app_id"):
                    return self._xml(f'<active-app><app id="{state["app_id"]}" type="appl" version="1.0">'
                                     f'{state["app"]}</app></active-app>')
                return self._xml("<active-app><app>Roku</app></active-app>")
            if p == "/query/media-player":
                title = f"<title>{state['title']}</title>" if state.get("title") else ""
                return self._xml(f"""<player error="false" state="{state.get('player_state', 'none')}">
<plugin bandwidth="1 bps" id="{state.get('app_id', '')}" name="{state.get('app', '')}"/>
<position>{state.get('pos_ms', 0)} ms</position><duration>{state.get('dur_ms', 0)} ms</duration>
<is_live>false</is_live>{title}</player>""")
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            state.setdefault("posts", []).append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    return _Server(H)


def fake_json_server(routes: dict, require: dict | None = None) -> _Server:
    """GET path -> JSON body. `require` = {header: value} the server insists on (else 401)."""

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            p = self.path.split("?")[0]
            for k, v in (require or {}).items():
                if p != "/identity" and v not in (self.headers.get(k) or ""):
                    self.send_response(401)
                    self.end_headers()
                    return
            if p not in routes:
                self.send_response(404)
                self.end_headers()
                return
            body = routes[p]() if callable(routes[p]) else routes[p]
            b = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

    return _Server(H)


def dev(kind="fake", name="Living Room", **kw) -> Device:
    return Device(kind=kind, name=name, host=kw.pop("host", "127.0.0.1"), **kw)
