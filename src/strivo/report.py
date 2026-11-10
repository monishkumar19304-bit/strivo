"""Opt-in compatibility reports: a prefilled GitHub issue link the user reviews and submits themselves.

Strivo never posts anything and holds no GitHub token. The report carries only: adapter, platform, OS family,
the app's public service name, the failure type, and versions. Never titles, names, addresses, device ids,
serials, accounts or tokens. `sanitize()` enforces that, and tests check it.
"""
from __future__ import annotations

import re
import time
import urllib.parse

from . import __version__
from . import rules as rules_mod
from .apps import service_for

REPO = "nickdnj/strivo"
FAILURES = {"degraded": "Titles stopped coming through", "verifying": "Still no titles after a rules update",
            "offline": "Box can't be reached", "unpaired": "Box refused Strivo's credentials"}
_PLATFORM = {"appletv": "Apple TV", "android": "Google TV / Android TV", "firetv": "Fire TV", "roku": "Roku",
             "plex": "Plex", "jellyfin": "Jellyfin"}


def os_family(os_version: str) -> str:
    """'Fire OS (Android 9)' -> 'Fire OS', 'Android 14' -> 'Android 14', '27.3' (tvOS) -> '27'."""
    v = (os_version or "").strip()
    if not v:
        return "unknown"
    if v.lower().startswith("fire os"):
        return "Fire OS"
    m = re.match(r"([A-Za-z ]*?)\s*(\d+)", v)
    if not m:
        return "unknown"
    return (m.group(1).strip() + " " + m.group(2)).strip()


def _os_label(plat: str, fam: str) -> str:
    if fam.isdigit():   # bare numbers come from boxes that report only a version
        return {"appletv": "tvOS", "roku": "Roku OS", "plex": "Plex", "jellyfin": "Jellyfin"}.get(plat, "version") + " " + fam
    return fam


def public_app(app: str) -> str:
    """Only apps in the public catalog are named; anything else is just 'another app'."""
    return service_for(app) or "another app"


def sanitize(entry: dict, failure: str) -> dict:
    plat = entry.get("platform") or entry.get("kind") or ""
    days = None
    if entry.get("last_good"):
        days = max(0, int((time.time() - entry["last_good"]) // 86400))
    return {"adapter": entry.get("kind") or "", "platform": _PLATFORM.get(plat, plat or "unknown"),
            "os": _os_label(plat, os_family(entry.get("os") or (entry.get("good_versions") or {}).get("os", ""))),
            "app": public_app(entry["app"]) if entry.get("app") else None, "failure": failure,
            "expected": entry.get("expected"), "observed": entry.get("last_level"),
            "days_since_last_good": days, "strivo": __version__, "rules": rules_mod.current().version}


def issue_url(entry: dict, failure: str, repo: str = REPO) -> tuple[str, dict]:
    s = sanitize(entry, failure)
    what = s["app"] or s["platform"]
    title = f"[compat] {what} on {s['platform']}: {FAILURES.get(failure, failure).lower()}"
    lines = ["**Automatic compatibility report** (prepared by Strivo, reviewed and submitted by a person).", "",
             "| | |", "|---|---|"]
    for k in ("adapter", "platform", "os", "app", "failure", "expected", "observed", "days_since_last_good",
              "strivo", "rules"):
        if s.get(k) not in (None, ""):
            lines.append(f"| {k.replace('_', ' ')} | {s[k]} |")
    lines += ["", "_Contains no titles, names, addresses, device ids or tokens. Add anything else you noticed below._", ""]
    q = urllib.parse.urlencode({"title": title, "body": "\n".join(lines), "labels": "compatibility"})
    return f"https://github.com/{repo}/issues/new?{q}", s
