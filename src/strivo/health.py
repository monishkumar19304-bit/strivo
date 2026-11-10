"""Health model: is tracking actually working, per box and per app?

States
- box:  healthy · degraded (an app on it is) · offline (can't reach it) · unpaired (it refused our credentials)
- app:  healthy (reported what we expect) · degraded (seen playing, but titles stopped when the rules or its own
        history say they should come through) · verifying (a fix was installed, waiting for proof) ·
        recovered · app-only (never reports titles here: a known limit, not a failure) · unknown

Rules that keep this honest:
- **No viewing is never "broken".** Only observations of an app actually playing count.
- One bad observation isn't a failure: an app must be seen playing without a title in 3 separate sessions
  (>= 10 minutes apart) before it's called degraded. Movies on a show app, menus and ads don't trip it.
- An update doesn't prove a fix: after new rules arrive, a degraded app is "verifying" until a real title
  comes through again.
"""
from __future__ import annotations

import threading
import time

from . import __version__
from . import rules as rules_mod
from .adapters.base import Device, NowPlaying
from .probe import classify

GOOD = {"title", "video", "channel", "episode"}
BAD_SESSIONS = 3          # degraded after this many separate app-only sessions...
SESSION_GAP = 10 * 60     # ...counted at most once per this many seconds
AUTH_WORDS = ("refused the token", "unauthor", "not paired", "pair again", "credential", "(401)", "(403)",
              "authentication", "auth failed")


def platform_of(device: Device) -> str:
    if device.kind == "android":
        return "firetv" if device.platform == "firetv" else "android"
    return device.kind


class HealthTracker:
    def __init__(self, data, save_every: float = 60):
        self.data = data
        self.lock = threading.Lock()
        self.state = data.read("health.json", {}) or {}
        self.state.setdefault("apps", {})
        self.save_every = save_every
        self._last_save = 0.0
        self.events: list[dict] = []   # state changes, for notifications

    # ---- observations (collector listener)
    def observe(self, device: Device, np: NowPlaying, now: float | None = None):
        now = time.time() if now is None else now
        row = np.to_event()
        lvl = classify(row)
        if lvl is None:
            return   # nothing playing: says nothing about health
        app = row.get("app") or row.get("app_id") or "unknown app"
        plat = platform_of(device)
        rid = device.extra.get("server") or device.id
        k = f"{rid}|{app}"
        with self.lock:
            e = self.state["apps"].setdefault(k, dict(
                device=rid, kind=device.kind, platform=plat, app=app, state="unknown", first_seen=now,
                last_seen=0, last_good=0, bad_streak=0, last_bad=0, best=None, degraded_since=None))
            before = e["state"]
            e["last_seen"] = now
            e["last_level"] = lvl
            expected = rules_mod.current().expect(plat, app)
            e["expected"] = expected
            if lvl in GOOD:
                e.update(last_good=now, bad_streak=0, degraded_since=None, best=lvl if e["best"] != "episode" else "episode",
                         good_versions=dict(strivo=__version__, rules=rules_mod.current().version,
                                            os=device.os_version or ""))
                e["state"] = "recovered" if before in ("degraded", "verifying") else "healthy"
            else:
                should_report = (expected in GOOD) or e["best"] in GOOD
                if not should_report:
                    e["state"] = "app-only"
                elif now - e["last_bad"] >= SESSION_GAP:
                    e["bad_streak"] += 1
                    e["last_bad"] = now
                    if e["bad_streak"] >= BAD_SESSIONS and before not in ("degraded", "verifying"):
                        e["state"] = "degraded"
                        e["degraded_since"] = e["degraded_since"] or now
            if e["state"] != before:
                self.events.append(dict(key=k, app=app, device=rid, frm=before, to=e["state"], at=now))
                self._save(force=True)
            else:
                self._save()

    def mark_verifying(self) -> int:
        """New rules installed: every degraded app now waits for proof that it recovered."""
        n = 0
        with self.lock:
            for e in self.state["apps"].values():
                if e["state"] == "degraded":
                    e["state"] = "verifying"
                    e["bad_streak"] = 0
                    n += 1
            self._save(force=True)
        return n

    def _save(self, force=False):
        if force or time.time() - self._last_save > self.save_every:
            self.data.write("health.json", self.state)
            self._last_save = time.time()

    def flush(self):
        with self.lock:
            self._save(force=True)

    # ---- views
    def apps(self) -> list[dict]:
        return sorted((dict(v, key=k) for k, v in self.state["apps"].items()), key=lambda x: (x["device"], x["app"]))

    def boxes(self, devices: list[Device], live: dict) -> list[dict]:
        apps = self.apps()
        out = []
        for d in devices:
            lv = live.get(d.id, {})
            err = (lv.get("error") or "").lower()
            if not d.enabled:
                st = "paused"
            elif lv.get("online"):
                st = "degraded" if any(a["device"] == d.id and a["state"] in ("degraded", "verifying") for a in apps) \
                    else "healthy"
            elif err and any(w in err for w in AUTH_WORDS):
                st = "unpaired"
            elif lv:
                st = "offline"
            else:
                st = "unknown"
            out.append(dict(id=d.id, name=d.name, kind=d.kind, platform=platform_of(d), state=st,
                            error=lv.get("error", ""), seen=lv.get("seen", 0), os=d.os_version or ""))
        return out
