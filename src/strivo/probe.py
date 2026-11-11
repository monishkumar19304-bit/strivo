"""`strivo probe`: what does each box and app actually report?

It listens for a few minutes (or reads an existing events.jsonl) and prints, per box and per app, whether
Strivo got the show and episode, just a title, or nothing at all. The report is built to be pasted in public:
no titles, no device names, no addresses, no account names. Only box models, OS versions, app names and counts.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections import defaultdict

from . import __version__
from .adapters.base import PAUSED, PLAYING, Device, NowPlaying
from .history import norm

LEVELS = ["episode", "title", "channel", "video", "app-only"]
LABEL = {"episode": "✅ show + episode", "title": "🟡 title only", "channel": "✅ live channel",
         "video": "✅ video title", "app-only": "⚪ app only (no title)"}
_SEASON = re.compile(r"\b(Season|S)\s*(\d+)", re.I)
_EPISODE = re.compile(r"\b(Episode|Ep\.?|E)\s*(\d+)", re.I)


def classify(row: dict) -> str | None:
    """row: an event dict (app, state, title, series, season, episode, artist...). None = nothing playing."""
    if row.get("state") not in (PLAYING, PAUSED):
        return None
    n = norm(row)
    if n is None:
        return "app-only" if row.get("app") or row.get("app_id") else None
    return {"episode": "episode", "live": "channel", "video": "video"}.get(n["kind"], "title")


def _separator(text: str) -> str | None:
    """Turn a title boundary into a regex without retaining title text."""
    m = re.search(r"\s*[^A-Za-z0-9\s]+[\s]*$", text)
    if not m:
        return r"\s*" if text and text[-1:].isspace() else ""
    out = []
    for ch in m.group(0):
        out.append(r"\s*" if ch.isspace() else re.escape(ch))
    return "".join(out)


def _shape_separator(text: str) -> str:
    m = re.search(r"\s*[^A-Za-z0-9\s]+[\s]*$", text)
    if m:
        return m.group(0)
    m = re.search(r"\s+$", text)
    return m.group(0) if m else ""


def _rule_suggestion(title: str) -> dict | None:
    """Build a redacted episode rule from a title's structural markers."""
    season = _SEASON.search(title)
    episode = _EPISODE.search(title, season.end() if season else 0)
    if not season or not episode:
        return None
    before = _separator(title[:season.start()])
    between = _separator(title[season.end():episode.start()])
    if before is None or between is None:
        return None
    suffix = title[episode.end():]
    has_ept = bool(suffix.strip())
    match = "^(.+?)" + before + re.escape(season.group(1)) + r"\s*(\d+)" + between \
        + re.escape(episode.group(1)) + r"\s*(\d+)"
    shape = ("<show>" + _shape_separator(title[:season.start()]) + season.group(1) + "<season>" +
             _shape_separator(title[season.end():episode.start()]) + episode.group(1) + "<episode>")
    if has_ept:
        shape += " " + "<episode title>"
    if has_ept:
        match += r"\s*(.*)"
    match += "$"
    rule = dict(field="title", match=match, kind="episode",
                set=dict(series=1, season=2, ep=3, **({"ept": 4} if has_ept else {})))
    return dict(shape=shape.strip(), rule=rule)


class Probe:
    def __init__(self):
        self.boxes: dict[str, dict] = {}
        self.apps: dict[str, dict] = defaultdict(lambda: defaultdict(lambda: dict(
            n=0, levels=defaultdict(int), position=False, duration=False, app_id=None,
            rule_suggestions=[])))

    @staticmethod
    def _key(device: Device) -> str:
        return device.extra.get("server") or device.id   # media-server clients belong to their server

    def box(self, device: Device):
        k = self._key(device)
        if k not in self.boxes:
            self.boxes[k] = dict(kind=device.kind, model=device.model or "", os=device.os_version or "",
                                         platform=device.platform or "")
        return self.boxes[k]

    def add(self, device: Device, row: dict):
        self.box(device)
        lvl = classify(row)
        if lvl is None:
            return
        app = row.get("app") or row.get("app_id") or "unknown app"
        a = self.apps[self._key(device)][app]
        a["n"] += 1
        a["levels"][lvl] += 1
        a["position"] |= row.get("pos") is not None
        a["duration"] |= bool(row.get("total"))
        a["app_id"] = row.get("app_id") or a["app_id"]
        n = norm(row)
        if (n and n["kind"] == "title" and row.get("title") and row.get("season") is None
                and row.get("episode") is None):
            suggestion = _rule_suggestion(str(row["title"]).strip())
            if suggestion and suggestion not in a["rule_suggestions"]:
                a["rule_suggestions"].append(suggestion)

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
                for suggestion in a["rule_suggestions"]:
                    out.append(f"    Suggested title_rules entry (shape: {suggestion['shape']}):")
                    out.extend("    " + line for line in json.dumps(
                        {app: [suggestion["rule"]]}, indent=2, ensure_ascii=False).splitlines())
            out.append("")
        out.append("Legend: ✅ tracked automatically · 🟡 tracked, but episode needs matching · ⚪ app only, not tracked")
        out.append("Share it: https://github.com/nickdnj/strivo/issues/new?template=device_report.yml")
        return "\n".join(out)

    def as_json(self) -> dict:
        return dict(version=__version__, boxes=[dict(b, apps={k: dict(n=v["n"], levels=dict(v["levels"]),
                                                                       position=v["position"], duration=v["duration"],
                                                                       app_id=v["app_id"],
                                                                       title_rule_suggestions=list(v["rule_suggestions"]))
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
