"""Live sports: what's on now, later today and this week, and where you can watch it.

- The schedule comes from a provider (`espn.py`), fetched per league and per day, the same for everyone.
- "Where to watch" comes from the rules file (`sports.networks`): which services carry each channel. That table
  changes with every rights deal, so it's data and is fixed by a rules update, not new code.
- The household's choices (leagues, followed teams) stay in config.json. Nothing about them is ever sent anywhere.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .. import rules as _rules
from .espn import ESPN

log = logging.getLogger(__name__)
DAYS = 7
LIVE_TTL, TODAY_TTL, LATER_TTL = 45, 600, 3 * 3600


def norm(s: str) -> str:
    return " ".join(str(s).lower().replace("+", " plus").split())


class Sports:
    def __init__(self, data, provider=None, clock=time.time):
        self.data = data
        self.provider = provider or ESPN()
        self.clock = clock
        self.cache: dict[tuple[str, str], tuple[float, list]] = {}
        self.errors: dict[str, str] = {}
        self.lock = threading.Lock()

    # ---- the household's choices
    def leagues(self) -> list[dict]:
        return _rules.current().sports()["leagues"]

    def prefs(self) -> dict:
        c = self.data.config()
        known = {lg["id"] for lg in self.leagues()}
        on = c.get("sports_leagues")
        if on is None:
            on = [lg["id"] for lg in self.leagues() if lg.get("default")]
        return dict(leagues=[x for x in on if x in known], teams=list(c.get("sports_teams") or []))

    def save_prefs(self, body: dict) -> dict:
        known = {lg["id"] for lg in self.leagues()}

        def fn(c):
            if "leagues" in body:
                c["sports_leagues"] = [str(x) for x in body["leagues"] if str(x) in known][:40]
            if "follow" in body or "unfollow" in body:
                teams = [t for t in (c.get("sports_teams") or []) if isinstance(t, dict)]
                u = body.get("unfollow")
                if isinstance(u, dict):
                    teams = [t for t in teams if not (t.get("league") == u.get("league") and t.get("id") == str(u.get("id")))]
                f = body.get("follow")
                if isinstance(f, dict) and str(f.get("league")) in known and f.get("id") and \
                        not any(t.get("league") == f["league"] and t.get("id") == str(f["id"]) for t in teams):
                    teams.append({k: str(f.get(k, ""))[:300] for k in ("league", "id", "name", "abbr", "logo")})
                c["sports_teams"] = teams[:100]
        self.data.update_config(fn)
        return self.prefs()

    # ---- where to watch
    def where(self, channels: list[dict], mine: list[str]) -> dict:
        """Map the channels a game is on to services; the household's own services come first."""
        sp = _rules.current().sports()
        nets = {norm(k): (k, v) for k, v in sp["networks"].items()}
        alias = {norm(k): v for k, v in sp["aliases"].items()}
        my = {norm(s): s for s in mine}
        out, have, seen = [], [], set()
        for ch in channels:
            raw = ch.get("name", "")
            canon = alias.get(norm(raw), raw)
            hit = nets.get(norm(canon))
            if ch.get("type", "").lower() == "radio":
                continue
            if hit:
                label, svcs = hit
                svcs_mine = [my[norm(s)] for s in svcs if norm(s) in my]
                out.append(dict(channel=label, services=svcs, mine=svcs_mine, local=False))
                for s in svcs_mine:
                    if s not in seen:
                        seen.add(s)
                        have.append(s)
            else:
                # regional sports networks and team apps: depends on where you live
                out.append(dict(channel=raw, services=[], mine=[], local=ch.get("market", "National") != "National"))
        out.sort(key=lambda w: (not w["mine"], w["local"]))
        return dict(where=out, mine=have)

    # ---- schedule
    def _ttl(self, games: list, day: dt.date, today: dt.date) -> float:
        if any(g["state"] == "in" for g in games):
            return LIVE_TTL
        if day <= today:
            soon = self.clock() + 1800
            if any(g["state"] == "pre" and _ts(g["start"]) <= soon for g in games):
                return LIVE_TTL
            return TODAY_TTL
        return LATER_TTL

    def _day(self, lg: dict, day: dt.date, today: dt.date) -> list:
        key = (lg["path"], day.strftime("%Y%m%d"))
        now = self.clock()
        with self.lock:
            hit = self.cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
        try:
            games = self.provider.day(lg["path"], key[1])
            self.errors.pop(lg["id"], None)
        except Exception as e:  # noqa: BLE001
            log.info("sports: %s %s failed: %s", lg["id"], key[1], e)
            self.errors[lg["id"]] = f"Couldn't reach the {lg['name']} schedule"
            return hit[1] if hit else []
        for g in games:
            g["league"], g["league_name"], g["sport"] = lg["id"], lg["name"], lg.get("sport", "")
        with self.lock:
            self.cache[key] = (now + self._ttl(games, day, today), games)
        return games

    def games(self, leagues: list[str] | None = None, days: int = DAYS) -> dict:
        p = self.prefs()
        want = set(leagues or p["leagues"])
        lgs = [lg for lg in self.leagues() if lg["id"] in want]
        today = dt.datetime.fromtimestamp(self.clock()).date()
        span = [today + dt.timedelta(days=i) for i in range(-1, max(1, min(days, 14)))]
        jobs = [(lg, d) for lg in lgs for d in span]
        with ThreadPoolExecutor(max_workers=8) as ex:
            parts = list(ex.map(lambda j: self._day(j[0], j[1], today), jobs))
        mine = list(self.data.config().get("services") or [])
        now, seen, out = self.clock(), set(), []
        for games in parts:
            for g in games:
                k = (g["league"], g["id"])
                if k in seen:
                    continue
                seen.add(k)
                t = _ts(g["start"])
                # yesterday's games only while they're still on; today's finals stay for the scores
                if g["state"] == "post" and now - t > 18 * 3600:
                    continue
                if g["state"] == "pre" and t < now - 12 * 3600:
                    continue
                out.append(dict(g, **self.where(g["channels"], mine)))
        out.sort(key=lambda g: ({"in": 0, "pre": 1, "post": 2}.get(g["state"], 1), _ts(g["start"])))
        return dict(games=out, leagues=[dict(lg, on=lg["id"] in p["leagues"]) for lg in self.leagues()],
                    teams=p["teams"], services=mine, errors=[self.errors[i] for i in want if i in self.errors],
                    fetched=now)


def _ts(iso: str) -> float:
    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0
