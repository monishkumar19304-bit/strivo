"""`strivo probe`: what does each box and app actually report?

It listens for a few minutes (or reads an existing events.jsonl) and prints, per box and per app, whether
Strivo got the show and episode, just a title, or nothing at all. The report is built to be pasted in public:
no titles, no device names, no addresses, no account names. Only box models, OS versions, app names and counts.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict

from . import __version__
from .adapters.base import PAUSED, PLAYING, Device, NowPlaying
from .history import norm

LEVELS = ["episode", "title", "channel", "video", "app-only"]
LABEL = {"episode": "✅ show + episode", "title": "🟡 title only", "channel": "✅ live channel",
         "video": "✅ video title", "app-only": "⚪ app only (no title)"}


def classify(row: dict) -> str | None:
    """row: an event dict (app, state, title, series, season, episode, artist...). None = nothing playing."""
    if row.get("state") not in (PLAYING, PAUSED):
        return None
    n = norm(row) if row.get("title") else None
    if n is None:
        return "app-only" if row.get("app") or row.get("app_id") else None
    return {"episode": "episode", "live": "channel", "video": "video"}.get(n["kind"], "title")


class Probe:
    def __init__(self):
        self.boxes: dict[str, dict] = {}
        self.apps: dict[str, dict] = defaultdict(lambda: defaultdict(lambda: dict(
            n=0, levels=defaultdict(int), position=False, duration=False, app_id=None)))

    def box(self, device: Device):
        if device.id not in self.boxes:
            self.boxes[device.id] = dict(kind=device.kind, model=device.model or "", os=device.os_version or "",
                                         platform=device.platform or "")
        return self.boxes[device.id]

    def add(self, device: Device, row: dict):
        self.box(device)
        lvl = classify(row)
        if lvl is None:
            return
        app = row.get("app") or row.get("app_id") or "unknown app"
        a = self.apps[device.id][app]
        a["n"] += 1
        a["levels"][lvl] += 1
        a["position"] |= row.get("pos") is not None
        a["duration"] |= bool(row.get("total"))
        a["app_id"] = row.get("app_id") or a["app_id"]

    def on_np(self, device: Device, np: NowPlaying):
        self.add(device, np.to_event())

    def report(self, minutes: float | None = None) -> str:
        kinds = {"appletv": "Apple TV", "android": "Google TV / Android TV", "roku": "Roku", "plex": "Plex",
                 "jellyfin": "Jellyfin"}
        out = [f"Strivo probe report · Strivo {__version__}" + (f" · listened {minutes:g} min" if minutes else ""), ""]
        if not self.boxes:
            out.append("No boxes answered. Check `strivo setup` (or the Devices page) and try again.")
        for i, (dev_id, b) in enumerate(self.boxes.items(), 1):
            kind = "Fire TV" if b["platform"] == "firetv" else kinds.get(b["kind"], b["kind"])
            desc = ", ".join(x for x in (b["model"], b["os"]) if x)
            out.append(f"Box {i}: {kind}" + (f" ({desc})" if desc else ""))
            apps = self.apps.get(dev_id) or {}
            if not apps:
                out.append("  (nothing played while listening)")
            for app, a in sorted(apps.items()):
                best = next(lv for lv in LEVELS if a["levels"].get(lv))
                extra = []
                if a["position"]:
                    extra.append("position")
                if a["duration"]:
                    extra.append("duration")
                ident = f" [{a['app_id']}]" if a["app_id"] and a["app_id"] != app else ""
                out.append(f"  {app}{ident}: {LABEL[best]}" + (f" · {'+'.join(extra)}" if extra else "")
                           + f" · {a['n']} update{'s' if a['n'] != 1 else ''}")
            out.append("")
        out.append("Legend: ✅ tracked automatically · 🟡 tracked, but episode needs matching · ⚪ app only, not tracked")
        out.append("Share it: https://github.com/nickdnj/strivo/issues/new?template=device_report.yml")
        return "\n".join(out)

    def as_json(self) -> dict:
        return dict(version=__version__, boxes=[dict(b, apps={k: dict(n=v["n"], levels=dict(v["levels"]),
                                                                       position=v["position"], duration=v["duration"],
                                                                       app_id=v["app_id"])
                                                              for k, v in (self.apps.get(i) or {}).items()})
                                                for i, b in self.boxes.items()])


def from_events(path: str, devices: list[Device] | None = None) -> Probe:
    """Probe an existing events.jsonl (e.g. months of real history) without listening."""
    p = Probe()
    known = {d.id: d for d in devices or []}
    with open(path) as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("kind") != "play":
                continue
            dev = known.get(e.get("dev")) or Device(kind=e.get("src") or "appletv", name=str(e.get("dev")),
                                                     id=str(e.get("dev")))
            p.add(dev, e)
    return p


async def listen(collector, minutes: float, only: str | None = None) -> Probe:
    p = Probe()
    for d in collector.devices():
        if d.enabled and (not only or d.id == only):
            p.box(d)
    collector.listeners.append(p.on_np)
    if only:
        orig = collector.devices
        collector.devices = lambda: [d for d in orig() if d.id == only]
    await collector.start()
    t_end = time.time() + minutes * 60
    try:
        while time.time() < t_end:
            await asyncio.sleep(min(5, max(0.1, t_end - time.time())))
    finally:
        await collector.stop()
    return p
