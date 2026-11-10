"""Opt-in compatibility reports: a prefilled GitHub issue link the user reviews and submits themselves.

Strivo never posts anything and holds no GitHub token. The report carries only: adapter, platform, OS family,
the app's public service name, the failure type, and versions. Never titles, names, addresses, device ids,
serials, accounts or tokens. `sanitize()` enforces that, and tests check it.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.parse

from . import __version__
from . import rules as rules_mod
from .apps import service_for
from .updates import http_get

REPO = "nickdnj/strivo"
SEARCH_CACHE_SECONDS = 300
SEARCH_TIMEOUT = 5
FAILURES = {"degraded": "Titles stopped coming through", "verifying": "Still no titles after a rules update",
            "offline": "Box can't be reached", "unpaired": "Box refused Strivo's credentials"}
_PLATFORM = {"appletv": "Apple TV", "android": "Google TV / Android TV", "firetv": "Fire TV", "roku": "Roku",
             "plex": "Plex", "jellyfin": "Jellyfin"}
_search_cache = {}
_search_cache_lock = threading.Lock()
log = logging.getLogger(__name__)


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


def _search_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def _issue_url(url: str, repo: str) -> str | None:
    parsed = urllib.parse.urlsplit(url or "")
    if parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query or parsed.fragment:
        return None
    expected = f"/{repo}/issues/"
    if not parsed.path.startswith(expected) or not re.fullmatch(r"\d+", parsed.path[len(expected):]):
        return None
    return url


def _search_get(url, headers):
    """A person is waiting on the button: give GitHub a few seconds, then fall back to a new report."""
    return http_get(url, headers, timeout=SEARCH_TIMEOUT)


def find_existing_issue(entry: dict, failure: str, repo: str = REPO, fetch=None) -> str | None:
    """Find a matching open compatibility issue without sending any report data."""
    s = sanitize(entry, failure)
    if entry.get("app") and s["app"] == "another app":
        return None
    what = s["app"] or s["platform"]
    description = FAILURES.get(failure, failure)
    fetch = fetch or _search_get
    cache_key = (repo, what, s["platform"], failure, id(fetch))
    now = time.monotonic()
    with _search_cache_lock:
        cached = _search_cache.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]

    query = " ".join((f"repo:{repo}", "is:issue", "is:open", "label:compatibility",
                      f'"{what}"', f'"{s["platform"]}"', f'"{description}"'))
    url = "https://api.github.com/search/issues?" + urllib.parse.urlencode({"q": query, "per_page": 10})
    headers = {"User-Agent": f"Strivo/{__version__}", "Accept": "application/vnd.github+json"}
    result = None
    try:
        code, _, body = fetch(url, headers)
        if code == 200:
            payload = json.loads(body or b"{}")
            required = [_search_text(what), _search_text(s["platform"]), _search_text(description),
                        _search_text(failure)]
            for item in payload.get("items", []):
                labels = {_search_text(label.get("name", "")) for label in item.get("labels", [])}
                if item.get("pull_request") or item.get("state") != "open" or "compatibility" not in labels:
                    continue
                text = _search_text((item.get("title") or "") + " " + (item.get("body") or ""))
                if all(value in text for value in required):
                    result = _issue_url(item.get("html_url"), repo)
                    if result:
                        break
        else:
            log.warning("GitHub compatibility search returned %s", code)
    except Exception as e:  # noqa: BLE001
        log.warning("GitHub compatibility search failed: %s", type(e).__name__)

    with _search_cache_lock:
        _search_cache[cache_key] = (now + SEARCH_CACHE_SECONDS, result)
    return result


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
