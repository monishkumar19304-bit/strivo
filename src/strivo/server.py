"""The web server: the phone app, its JSON API, the setup wizard API and media-server webhooks.

Standard library only (ThreadingHTTPServer). Adapter work runs on the collector's asyncio loop.
Strivo is meant for your home network: there are no accounts, so don't expose this port to the internet.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

from . import __version__, diagnose, picks, report
from . import rules as rules_mod
from .health import HealthTracker
from .updates import UpdateChecker
from .adapters.base import AdapterError
from .adapters.jellyfin import WEBHOOK_TEMPLATE
from . import apps
from .collector import Collector, Runtime
from .config import DISC_DEFAULT, DataDir
from .core import REASONS, Core
from .devices import DeviceManager
from .sports import Sports

log = logging.getLogger(__name__)
WEB = resources.files("strivo") / "web"
CTYPES = {"png": "image/png", "js": "application/javascript", "webmanifest": "application/manifest+json",
          "svg": "image/svg+xml", "html": "text/html; charset=utf-8", "css": "text/css"}


def parse_multipart(body: bytes, content_type: str) -> dict:
    """Just enough multipart/form-data to read Plex's webhook ("payload" field + a thumbnail we ignore)."""
    m = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not m:
        return {}
    out = {}
    for part in body.split(b"--" + m.group(1).encode()):
        head, _, val = part.partition(b"\r\n\r\n")
        nm = re.search(rb'name="([^"]+)"', head)
        if nm and b"filename=" not in head:
            out[nm.group(1).decode()] = val.rstrip(b"\r\n-").decode("utf-8", "replace")
    return out


class App:
    """Everything the server needs, wired together. Tests build one with fakes."""

    def __init__(self, data_dir, transport=None, adapters=None, start_background=True, fetch=None, sports_provider=None):
        self.data = DataDir(data_dir)
        self.rules = rules_mod.use(rules_mod.Rules(self.data.path, __version__))
        self.runtime = Runtime().start()
        self.collector = Collector(self.data, adapters)
        self.core = Core(self.data, transport, names=self.collector.name_of)
        self.devices = DeviceManager(self.data, self.collector, self.runtime)
        self.tracker = HealthTracker(self.data)
        self.collector.listeners.append(self.tracker.observe)
        self.updates = UpdateChecker(self.data, self.rules, self.tracker, self.core.notify, fetch=fetch or update_fetch)
        self.sports = Sports(self.data, sports_provider)
        self.started = time.time()
        if start_background:
            self.runtime.call(self.collector.start(), 10)
            threading.Thread(target=self._wishlist_loop, daemon=True).start()
            threading.Thread(target=self._picks_loop, daemon=True).start()
            threading.Thread(target=self._health_loop, daemon=True).start()

    def _wishlist_loop(self):
        time.sleep(120)
        while True:
            try:
                if self.core.tmdb.configured:
                    self.core.wishlist_check()
            except Exception:  # noqa: BLE001
                log.exception("wishlist check failed")
            time.sleep(6 * 3600)

    def _picks_loop(self):
        time.sleep(300)
        while True:
            picks.build_all(self.core)
            time.sleep(24 * 3600)

    def _health_loop(self):
        """Every minute: tell the household about real state changes; once a day: look for fixes."""
        time.sleep(60)
        while True:
            try:
                self.notify_health_changes()
                if self.data.config().get("update_checks", True) and self.updates.due():
                    self.updates.check()
            except Exception:  # noqa: BLE001
                log.exception("health loop failed")
            time.sleep(60)

    def notify_health_changes(self):
        ev, self.tracker.events = self.tracker.events, []
        for e in ev:
            if e["to"] == "degraded":
                self.core.notify(f"Strivo: {e['app']} stopped reporting titles",
                                 "Strivo will look for a fix and tell you when it's working again.")
            elif e["to"] == "recovered":
                self.core.notify(f"Strivo: {e['app']} is tracking again", "The fix worked.")

    def doctor(self) -> dict:
        devs = self.collector.devices()
        boxes = self.tracker.boxes(devs, self.collector.live)
        os_of = {b["id"]: b["os"] for b in boxes}
        dg = diagnose.get(self.data.config())
        problems = []
        for b in boxes:
            if b["state"] in ("offline", "unpaired"):
                d = dg.diagnose(b, b["state"])
                problems.append(dict(key=b["id"], what=b["name"], state=b["state"], summary=d.summary,
                                     cause=d.likely_cause, steps=d.next_steps))
        apps = self.tracker.apps()
        for a in apps:
            a["os"] = os_of.get(a["device"], "")
            a["device_name"] = self.collector.name_of(a["device"])
            if a["state"] in ("degraded", "verifying"):
                d = dg.diagnose(a, a["state"])
                problems.append(dict(key=a["key"], what=f"{a['app']} on {a['device_name']}", state=a["state"],
                                     summary=d.summary, cause=d.likely_cause, steps=d.next_steps))
        c = self.data.config()
        return dict(boxes=boxes, apps=apps, problems=problems, rules=self.rules.summary(), updates=self.updates.state(),
                    settings=dict(rules_updates=c.get("rules_updates", "auto"),
                                  update_checks=c.get("update_checks", True)), diagnosis_tier=dg.tier)

    def report_url(self, key: str) -> dict:
        doc = self.doctor()
        a = next((x for x in doc["apps"] if x["key"] == key), None)
        if a:
            failure = a["state"] if a["state"] in report.FAILURES else "degraded"
        else:
            a = next((dict(b, app=None) for b in doc["boxes"] if b["id"] == key), None)
            if a is None:
                raise KeyError(key)
            failure = a["state"] if a["state"] in report.FAILURES else "offline"
        url, fields = report.issue_url(a, failure)
        existing = report.find_existing_issue(a, failure)
        return dict(url=existing or url, fields=fields, existing=bool(existing))

    def setup_state(self) -> dict:
        c = self.data.config()
        return dict(version=__version__, rules_version=self.rules.version, setup_done=bool(c.get("setup_done")), tmdb_key_set=self.core.tmdb.configured,
                    country=c.get("country", "US"), services=c.get("services") or [], all_services=apps.streaming(),
                    default_tv=c.get("default_tv", ""),
                    devices=[dict(d, kind_label=getattr(self.collector.adapters.get(d["kind"]), "label", d["kind"]))
                             for d in c.get("devices", [])],
                    adapters=[dict(kind=k, label=a.label, experimental=a.experimental) for k, a in
                              self.collector.adapters.items()],
                    jellyfin_template=WEBHOOK_TEMPLATE)

    def health(self) -> dict:
        out = []
        for d in self.collector.devices():
            lv = self.collector.live.get(d.id, {})
            out.append(dict(id=d.id, name=d.name, kind=d.kind, enabled=d.enabled, online=lv.get("online", False),
                            seen=lv.get("seen", 0), error=lv.get("error", ""),
                            app=(lv.get("now") or {}).get("app") or "", state=(lv.get("now") or {}).get("state") or ""))
        # media-server clients show up as their own sources
        for k, lv in self.collector.live.items():
            if "--" in k:
                out.append(dict(id=k, name=lv.get("name", k), kind=lv.get("kind"), enabled=True, online=True,
                                seen=lv.get("seen", 0), error="", app=(lv.get("now") or {}).get("app") or "",
                                state=(lv.get("now") or {}).get("state") or ""))
        return dict(uptime=round(time.time() - self.started), devices=out)

    def close(self):
        try:
            self.runtime.call(self.collector.stop(), 10)
            for a in self.collector.adapters.values():
                self.runtime.call(a.close(), 10)
        except Exception:
            pass
        self.runtime.stop()


def make_handler(app: App):
    core = app.core

    class H(BaseHTTPRequestHandler):
        server_version = "Strivo/" + __version__

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        def send(self, code, body, ctype="application/json"):
            if not isinstance(body, (bytes, str)):
                body = json.dumps(body, default=str)
            b = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(b)

        def ok(self, obj=None):
            self.send(200, obj if obj is not None else {"ok": True})

        def static(self, name):
            f = WEB / name
            if not f.is_file():
                return self.send(404, "not found", "text/plain")
            self.send(200, f.read_bytes(), CTYPES.get(name.rsplit(".", 1)[-1], "application/octet-stream"))

        # -------------------------------------------------------------- POST
        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            p = u.path
            try:
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
                if p == "/api/webhook/plex":
                    form = parse_multipart(raw, self.headers.get("Content-Type", ""))
                    payload = json.loads(form.get("payload") or raw or b"{}")
                    return self.ok({"ok": True, "n": app.collector.ingest("plex", payload)})
                body = json.loads(raw or b"{}")
                if p == "/api/webhook/jellyfin":
                    return self.ok({"ok": True, "n": app.collector.ingest("jellyfin", body)})
                if p.startswith("/api/setup"):
                    return self.setup_post(p, body)
                if p == "/api/doctor/check":
                    app.updates.check(force=True)
                    return self.ok(app.doctor())
                if p == "/api/doctor/apply":
                    try:
                        app.updates.apply_pending()
                    except rules_mod.RulesError as e:
                        return self.send(400, {"error": str(e)})
                    return self.ok(app.doctor())
                if p == "/api/doctor/rollback":
                    app.rules.rollback()
                    return self.ok(app.doctor())
                if p == "/api/wish":
                    items = core.wishlist()
                    if body.get("action") == "remove":
                        items = [w for w in items if not (w["id"] == body["id"] and w["type"] == body["type"])]
                    elif not any(w["id"] == body["id"] and w["type"] == body["type"] for w in items):
                        items.insert(0, dict(id=int(body["id"]), type=str(body["type"]), title=str(body["title"])[:200],
                                             year=str(body.get("year", ""))[:4], poster=str(body.get("poster", ""))[:300],
                                             added=time.time()))
                    core.save_wishlist(items)
                    return self.ok()
                if p == "/api/tv/play" and body.get("dev"):
                    try:
                        what = app.runtime.call(app.devices.tv_play(str(body["dev"]), str(body.get("provider", "")),
                                                                    str(body.get("trailer", ""))), 90)
                        return self.ok(dict(result="ok", what=what))
                    except AdapterError as e:
                        return self.send(502, {"error": str(e)})
                    except Exception as e:  # noqa: BLE001
                        return self.send(502, {"error": f"TV did not respond ({type(e).__name__})"})
                if p in ("/api/shows/dismiss", "/api/shows/restore") and body.get("id"):
                    core.set_dismissed("showsug:%d" % int(body["id"]), p.endswith("dismiss"))
                    return self.ok()
                if p == "/api/settings":
                    return self.ok(core.settings_save(body))
                if p == "/api/sports/prefs":
                    return self.ok(app.sports.prefs() if not isinstance(body, dict) else app.sports.save_prefs(body))
                if p in ("/api/discover/dismiss", "/api/discover/restore") and body.get("id") \
                        and body.get("type") in ("tv", "movie"):
                    core.set_dismissed("disc:%s:%d" % (body["type"], int(body["id"])), p.endswith("dismiss"))
                    return self.ok()
                if p == "/api/discover/later" and body.get("id") and body.get("type") in ("tv", "movie"):
                    core.set_later("%s:%d" % (body["type"], int(body["id"])), body.get("on", True))
                    return self.ok()
                if p == "/api/shows/later" and body.get("id"):
                    core.set_later(int(body["id"]), body.get("on", True))
                    return self.ok()
                if p in ("/api/queue/add", "/api/queue/remove") and body.get("id"):
                    q = [x for x in core.queue() if x["id"] != int(body["id"])]
                    if p.endswith("add"):
                        q.insert(0, dict(id=int(body["id"]), title=str(body.get("title", ""))[:200],
                                         year=str(body.get("year", ""))[:4], poster=str(body.get("poster", ""))[:300],
                                         providers=[str(x) for x in body.get("providers", [])][:8], added=time.time()))
                    core.save_queue(q)
                    return self.ok()
                if p == "/api/suggest/feedback" and body.get("id") and body.get("reason") in REASONS:
                    core.save_feedback(int(body["id"]), str(body.get("title", ""))[:200], body["reason"],
                                       body.get("note"), body.get("tags") or [], body.get("genres") or [],
                                       "tv" if body.get("type") == "tv" else "movie")
                    return self.ok()
                if p in ("/api/suggest/dismiss", "/api/suggest/restore") and body.get("id"):
                    core.set_dismissed("suggest:%d" % int(body["id"]), p.endswith("dismiss"))
                    return self.ok()
                if p in ("/api/remove", "/api/restore") and body.get("kind") in ("series", "movie", "daily") \
                        and body.get("name"):
                    core.set_dismissed(body["kind"] + ":" + body["name"], p == "/api/remove")
                    return self.ok()
                self.send(400, {"error": "bad request"})
            except Exception as e:  # noqa: BLE001
                log.exception("POST %s failed", p)
                self.send(500, {"error": type(e).__name__})

        def setup_post(self, p, body):
            dm = app.devices
            if p == "/api/setup/discover":
                return self.ok({"found": app.runtime.call(dm.discover(float(body.get("timeout", 5))), 40)})
            if p == "/api/setup/pair":
                spec = {k: body.get(k) for k in ("kind", "name", "host", "port", "identifier", "platform", "model")
                        if body.get(k) not in (None, "")}
                if not spec.get("kind") or not spec.get("host"):
                    return self.send(400, {"error": "kind and host are needed"})
                spec.setdefault("name", spec["kind"])
                try:
                    return self.ok(dm.start_pairing(spec).view())
                except AdapterError as e:
                    return self.send(400, {"error": str(e)})
            if p == "/api/setup/pair/input":
                pr = dm.pairings.get(str(body.get("id")))
                if not pr:
                    return self.send(404, {"error": "no such pairing"})
                pr.answer(str(body.get("value", "")).strip())
                return self.ok(pr.view())
            if p == "/api/setup/tmdb":
                key = str(body.get("key", "")).strip()
                from .tmdb import TMDB
                if not key or not TMDB(key, core.tmdb.transport).check():
                    return self.send(400, {"error": "TMDB didn't accept that key. Copy the \"API Read Access "
                                                    "Token\" (or the API key) from themoviedb.org > Settings > API."})
                app.data.set_secret("tmdb_key", key)
                return self.ok({"ok": True, "tmdb_key_set": True})
            if p == "/api/setup/device":
                ok = dm.update_device(str(body.get("id", "")), body.get("name"), body.get("enabled"),
                                      bool(body.get("remove")))
                return self.ok(app.setup_state()) if ok else self.send(404, {"error": "no such device"})
            if p == "/api/setup/prefs":
                core.settings_save({k: body[k] for k in ("country", "my_services", "default_tv") if k in body})
                return self.ok(app.setup_state())
            if p == "/api/setup/done":
                app.data.update_config(lambda c: c.__setitem__("setup_done", True))
                threading.Thread(target=picks.build_all, args=(core,), daemon=True).start()
                return self.ok(app.setup_state())
            return self.send(404, {"error": "unknown"})

        # -------------------------------------------------------------- GET
        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            p = u.path
            qs = urllib.parse.parse_qs(u.query)
            q1 = lambda k, d="": qs.get(k, [d])[0]  # noqa: E731
            try:
                if p in ("/", "/index.html"):
                    return self.static("index.html")
                if p == "/sw.js":
                    return self.static("sw.js")
                if p == "/manifest.webmanifest":
                    return self.static("manifest.webmanifest")
                if p.startswith("/pwa/"):
                    name = p.rsplit("/", 1)[-1]
                    if not re.fullmatch(r"[\w.-]+", name):
                        return self.send(404, "not found", "text/plain")
                    return self.static("pwa/" + name)
                if p == "/api/data":
                    return self.ok(core.build())
                if p == "/api/setup":
                    return self.ok(app.setup_state())
                if p == "/api/setup/pair":
                    pr = app.devices.pairings.get(q1("id"))
                    return self.ok(pr.view()) if pr else self.send(404, {"error": "no such pairing"})
                if p == "/api/health":
                    return self.ok(app.health())
                if p == "/api/doctor":
                    return self.ok(app.doctor())
                if p == "/api/doctor/report":
                    try:
                        return self.ok(app.report_url(q1("key")))
                    except KeyError:
                        return self.send(404, {"error": "unknown"})
                if p == "/api/poster":
                    q, typ = q1("q"), q1("type")
                    return self.ok({"url": core.poster(q, typ if typ in ("tv", "movie") else "") if q else None})
                if p == "/api/search":
                    q = q1("q")
                    return self.ok(core.search(q) if len(q.strip()) > 1 and core.tmdb.configured else [])
                if p == "/api/suggest":
                    return self.ok(picks.suggestions(core))
                if p == "/api/tvs":
                    try:
                        return self.ok({"devices": app.runtime.call(app.devices.tv_list(), 30)})
                    except Exception as e:  # noqa: BLE001
                        return self.send(502, {"error": type(e).__name__, "devices": []})
                if p == "/api/settings":
                    return self.ok(core.settings_view())
                if p == "/api/sports":
                    lg = [x for x in q1("leagues").split(",") if x]
                    return self.ok(app.sports.games(lg or None, int(q1("days", "7") or 7)))
                if p == "/api/discover":
                    cat = q1("cat")
                    if cat == "series":
                        return self.ok(picks.shows(core))
                    if cat == "movies":
                        return self.ok(picks.suggestions(core))
                    if cat not in DISC_DEFAULT:
                        return self.send(404, {"error": "unknown"})
                    return self.ok(picks.disc_list(core, cat))
                if p == "/api/discover/refresh":
                    threading.Thread(target=picks.build_all, args=(core, q1("cat") or None), daemon=True).start()
                    return self.ok()
                if p == "/api/shows":
                    return self.ok(picks.shows(core))
                if p == "/api/shows/refresh":
                    threading.Thread(target=picks.shows_build, args=(core,), daemon=True).start()
                    return self.ok()
                if p == "/api/suggest/refresh":
                    threading.Thread(target=picks.suggest_build, args=(core,), daemon=True).start()
                    return self.ok()
                if p in ("/api/suggest/extras", "/api/suggest/detail"):
                    try:
                        mid, typ = int(q1("id", "0")), ("tv" if q1("type") == "tv" else "movie")
                        return self.ok(core.extras(mid, typ) if p.endswith("extras") else core.where(mid, typ))
                    except Exception:  # noqa: BLE001
                        return self.send(404, {"error": "not found"})
                if p == "/api/wishlist":
                    return self.ok(core.wishlist_view())
                if p == "/api/show":
                    q = q1("q")
                    return self.ok(core.show(q) if q else None)
                self.send(404, "not found", "text/plain")
            except Exception as e:  # noqa: BLE001
                log.exception("GET %s failed", p)
                self.send(500, {"error": type(e).__name__})

    return H


def update_fetch(url, headers):
    from .updates import http_get
    return http_get(url, headers)


def serve(data_dir, host: str = "0.0.0.0", port: int = 8091, advertise: bool = True):
    from .discovery import LocalName
    app = App(data_dir)
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    httpd.daemon_threads = True
    name = LocalName(port) if advertise else None
    if name:
        name.start()
    log.info("Strivo %s on http://%s:%s (data: %s)", __version__, host, port, app.data.path)
    print(f"Strivo is running. Open http://strivo.local:{port} (or http://<this computer's IP>:{port}) on your phone.",
          flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if name:
            name.stop()
        httpd.server_close()
        app.close()
