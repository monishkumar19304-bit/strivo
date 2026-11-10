"""Schedules and live scores from ESPN's public scoreboard feed.

The feed is free and needs no key, but it isn't an official, documented API, so everything that depends on its
shape lives in this one file. Requests are made per league and per day, the same for everyone: nothing about the
household (teams, history, location) is ever sent.
"""
from __future__ import annotations

import gzip
import json
import urllib.request

BASE = "https://site.api.espn.com/apis/site/v2/sports/"


def http_get(url: str, timeout: float = 12) -> dict:
    from ..net import ssl_context
    req = urllib.request.Request(url, headers={"Accept": "application/json", "Accept-Encoding": "gzip",
                                               "User-Agent": "Strivo"})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return json.loads(raw)


class ESPN:
    name = "espn"

    def __init__(self, fetch=None):
        self.fetch = fetch or http_get

    def day(self, path: str, yyyymmdd: str) -> list[dict]:
        """Every game in one league on one day, as plain Strivo game dicts."""
        d = self.fetch(f"{BASE}{path}/scoreboard?dates={yyyymmdd}&limit=300")
        return [g for g in (parse_event(e) for e in d.get("events") or []) if g]


def _competition(e: dict) -> dict:
    """Most events have one competition. Racing weekends have several sessions: pick the live one, else the next."""
    cs = e.get("competitions") or [{}]
    for want in ("in", "pre"):
        for c in cs:
            if ((c.get("status") or {}).get("type") or {}).get("state") == want:
                return c
    return cs[-1]


def _channels(c: dict) -> list[dict]:
    out, seen = [], set()
    for b in c.get("geoBroadcasts") or []:
        name = ((b.get("media") or {}).get("shortName") or "").strip()
        if name and name not in seen:
            seen.add(name)
            out.append(dict(name=name, type=((b.get("type") or {}).get("shortName") or "").title(),
                            market=((b.get("market") or {}).get("type") or "National").title()))
    for b in c.get("broadcasts") or []:     # older shape, or when geoBroadcasts is missing
        for name in b.get("names") or []:
            if name and name not in seen:
                seen.add(name)
                out.append(dict(name=name, type="", market=(b.get("market") or "National").title()))
    return out


def parse_event(e: dict) -> dict | None:
    if not isinstance(e, dict) or not e.get("id"):
        return None
    c = _competition(e)
    st = (c.get("status") or e.get("status") or {})
    stt = st.get("type") or {}
    comps = c.get("competitors") or []
    g = dict(id=str(e["id"]), name=e.get("name") or "", short=e.get("shortName") or e.get("name") or "",
             start=c.get("date") or e.get("date") or "", state=stt.get("state") or "pre",
             detail=stt.get("shortDetail") or stt.get("detail") or stt.get("description") or "",
             channels=_channels(c), venue=((c.get("venue") or {}).get("fullName") or ""),
             session=((c.get("type") or {}).get("abbreviation") or ""), teams=[], leaders=[])
    teams = [x for x in comps if x.get("team")]
    if len(comps) == 2 and len(teams) == 2:
        # away first, like the feed's "Away at Home" names
        for x in sorted(teams, key=lambda x: x.get("homeAway") != "away"):
            t = x["team"]
            g["teams"].append(dict(id=str(t.get("id", "")), name=t.get("displayName") or t.get("name") or "",
                                   short=t.get("shortDisplayName") or t.get("name") or "",
                                   abbr=t.get("abbreviation") or "", logo=t.get("logo") or "",
                                   home=x.get("homeAway") == "home", score=str(x.get("score", "") or ""),
                                   winner=bool(x.get("winner"))))
    else:
        # golf, racing: a field of players; keep the top of the leaderboard
        ranked = sorted(comps, key=lambda x: x.get("order") or 999)
        for x in ranked[:5]:
            a = x.get("athlete") or x.get("team") or {}
            nm = a.get("displayName") or a.get("fullName") or a.get("name") or ""
            if nm:
                g["leaders"].append(dict(name=nm, score=str(x.get("score", "") or "")))
    return g
