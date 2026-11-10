"""History: turn raw now-playing events into watch sessions, then into the queue, movies and Daily.

This is the heart of Strivo and the part most worth reading before you change behaviour. It works on any
event source; adapters only decide which fields they fill in.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from datetime import datetime

from . import rules as rules_mod

CAP = 4 * 3600          # a Playing state with no later event is trusted this long
JOIN = 20 * 60          # same item on the same TV within this gap is one session

_SE = re.compile(r"\bS(?:eason)?\s*(\d+)\s*[,:·.]?\s*E(?:p(?:isode)?\.?)?\s*(\d+)\b", re.I)


def is_news(rules, series, ch):
    t = (series or "").lower()
    return (ch or "") in rules["news_channels"] or any(k in t for k in rules["news_titles"])


def norm(e: dict):
    """Map one event's fields to (kind, series, season, ep, ept, ch, title).

    Each app formats now-playing differently; the per-app rules below are what households have measured.
    New rules come from `strivo probe` reports.
    """
    app = e.get("app") or ""
    t = (e.get("title") or "").strip()
    a = (e.get("artist") or "").strip()
    if not t:
        # Some apps move the name to another field after an update; a rules fix can point at it.
        for rule in rules_mod.current().title_rules(app):
            if rule.get("field", "title") != "title":
                hit = _apply_rule(rule, t, a, e)
                if hit and hit.get("series"):
                    hit["title"] = hit["series"]
                    return hit
        return None
    kind, series, season, ep, ept, ch = "title", t, None, None, None, ""
    if e.get("series") and (e.get("season") is not None or e.get("episode") is not None):
        # The box (or media server) told us exactly: trust it.
        return dict(kind="episode", series=e["series"].strip(), season=e.get("season"), ep=e.get("episode"),
                    ept=t if t != e["series"] else None, ch="", title=t)
    for rule in rules_mod.current().title_rules(app):
        hit = _apply_rule(rule, t, a, e)
        if hit:
            return hit
    if e.get("series"):
        kind, series, ept = "episode", e["series"].strip(), (t if t != e["series"] else None)
    else:
        # Generic: "Show S2 E5" in the title, or "S2:E5 Name" in the subtitle with the show as title.
        m = _SE.search(a) if a else None
        if m:
            kind, season, ep = "episode", int(m.group(1)), int(m.group(2))
            rest = _SE.sub("", a).strip(" -·:|")
            ept = rest or None
        else:
            m = _SE.search(t)
            if m:
                kind, season, ep = "episode", int(m.group(1)), int(m.group(2))
                series = t[:m.start()].strip(" -·:|") or t
                ept = t[m.end():].strip(" -·:|") or None
    return dict(kind=kind, series=series, season=season, ep=ep, ept=ept, ch=ch, title=t)


def _apply_rule(rule, t, a, e):
    """One data-driven rule from compat.json: optional regex on a field, then where each value comes from
    (a group number, or "@title" / "@artist" / "@album")."""
    fields = {"title": t, "artist": a, "album": (e.get("album") or "").strip()}
    m = None
    if rule.get("rx") is not None:
        m = rule["rx"].match(fields[rule.get("field", "title")])
        if not m:
            return None
    out = dict(kind=rule["kind"], series=t, season=None, ep=None, ept=None, ch="", title=t)
    for k, src in (rule.get("set") or {}).items():
        if isinstance(src, int):
            v = m.group(src) if m and src <= (m.re.groups or 0) else None
        else:
            v = fields.get(str(src).lstrip("@"))
        if k in ("season", "ep"):
            v = int(v) if v not in (None, "") and str(v).isdigit() else None
        out[k] = v if v is not None else ("" if k == "ch" else out.get(k))
    if out["kind"] == "episode" and not out["series"]:
        return None
    return out


def key(app, n):
    if n["kind"] in ("episode", "title"):
        return (app, n["kind"], n["series"], n["season"], n["ep"], n["ept"] or "")
    return (app, n["kind"], n["series"], n["ch"])


def _ts(e):
    t = e.get("_t")
    if t is None:
        t = e["_t"] = datetime.fromisoformat(e["ts"]).timestamp()
    return t


def norm_cached(e: dict):
    """norm(e), remembered on the event until the rules change (events are kept in memory between builds)."""
    rv = rules_mod.current().version
    if e.get("_nv") != rv:
        e["_n"], e["_nv"] = norm(e), rv
    return e["_n"]


def sessions_from_events(ev: list[dict], now: float | None = None):
    """Events -> (sessions, last_state, lastpos). Pure: no TMDB, no config. Easy to test."""
    now = time.time() if now is None else now
    ev = [e for e in ev if e.get("kind") == "play"]
    by = defaultdict(list)
    for e in ev:
        by[e["dev"]].append(e)
    segs, last_state = [], {}
    for dev, es in by.items():
        es.sort(key=lambda e: e["ts"])
        for i, e in enumerate(es):
            t = _ts(e)
            n = norm_cached(e) if e.get("title") else None
            if e.get("state") == "Playing" and n:
                nxt = _ts(es[i + 1]) if i + 1 < len(es) else min(now, t + CAP)
                dur = max(0, min(nxt - t, CAP))
                if n["kind"] != "live" and e.get("total") and e.get("pos") is not None:
                    dur = min(dur, max(0, e["total"] - e["pos"]) + 60)   # can't watch past the end
                segs.append(dict(dev=dev, app=e.get("app"), n=n, key=key(e.get("app"), n), t0=t, t1=t + dur,
                                 pos=e.get("pos"), total=e.get("total")))
        last = es[-1]
        if last.get("state") == "Playing" and now - _ts(last) < CAP:
            # apps that send no metadata (Netflix) still show as playing, just without a title
            n = norm_cached(last) or dict(kind="title", series=last.get("app"), season=None, ep=None, ept=None, ch="",
                                   title=last.get("app"))
            last_state[dev] = dict(app=last.get("app"), n=n, pos=last.get("pos"), total=last.get("total"))
    lastpos = {}
    for dev, es in by.items():
        for e in es:
            if e.get("title") and e.get("pos") is not None:
                n = norm_cached(e)
                if n:
                    lastpos[(dev, key(e.get("app"), n))] = (e["pos"], e.get("total"), e["ts"])
    segs.sort(key=lambda s: s["t0"])
    sessions, open_ = [], {}
    for s in segs:
        prev = open_.get((s["dev"], s["key"]))      # the latest session of this item on this TV
        if prev and s["t0"] - prev["t1"] <= JOIN:
            prev["t1"] = max(prev["t1"], s["t1"])
            prev["sec"] += s["t1"] - s["t0"]
            prev["pos"], prev["total"] = s["pos"], s["total"]
        else:
            sessions.append(dict(dev=s["dev"], app=s["app"], n=s["n"], key=s["key"], t0=s["t0"], t1=s["t1"],
                                 sec=s["t1"] - s["t0"], pos=s["pos"], total=s["total"]))
            open_[(s["dev"], s["key"])] = sessions[-1]
    for x in sessions:
        lp = lastpos.get((x["dev"], x["key"]))
        if lp and lp[0] is not None:
            x["pos"], x["total"] = lp[0], lp[1]
    return sessions, last_state, ev


class SessionBuilder:
    """sessions_from_events(), kept up to date one event at a time, so years of history never sit in memory.

    Feed it play events per device in time order (`feed`); ask for the result at any moment (`result`). It holds
    only finished sessions, the latest event per device and the latest position per item. An event older than its
    device's newest one can't be placed incrementally: `feed` returns False and the caller rebuilds from scratch.
    Gives exactly what sessions_from_events() gives for the same events (tests check this).
    """

    def __init__(self):
        self.sessions = []          # finished sessions, in the order they started per device
        self.open = {}              # (dev, key) -> latest session of that item on that TV
        self.pending = {}           # dev -> (event, n): the newest event; its length depends on what comes next
        self.lastpos = {}           # (dev, key) -> (pos, total, ts)
        self.last_ts = {}           # dev -> newest ts string fed
        self.first_ts = None
        self.seen = set()           # nm_key of every series/title ever played (for picks)
        self.count = 0

    def feed(self, e: dict) -> bool:
        if e.get("kind") != "play" or not e.get("ts"):
            return True
        dev, ts = e["dev"], e["ts"]
        if dev in self.last_ts and ts < self.last_ts[dev]:
            return False
        self.last_ts[dev] = ts
        if self.first_ts is None or ts < self.first_ts:
            self.first_ts = ts
        self.count += 1
        t = _ts(e)
        n = norm(e) if e.get("title") else None
        for f in ("series", "title"):
            if e.get(f):
                self.seen.add(nm_key(e[f]))
        prev = self.pending.get(dev)
        if prev:
            self._finish(dev, prev[0], prev[1], nxt=t)
        self.pending[dev] = (_slim(e, t), n)
        if n and e.get("pos") is not None:
            self.lastpos[(dev, key(e.get("app"), n))] = (e["pos"], e.get("total"), ts)
        return True

    def _seg(self, dev, e, n, nxt):
        t = e["_t"]
        dur = max(0, min(nxt - t, CAP))
        if n["kind"] != "live" and e.get("total") and e.get("pos") is not None:
            dur = min(dur, max(0, e["total"] - e["pos"]) + 60)   # can't watch past the end
        return dict(dev=dev, app=e.get("app"), n=n, key=key(e.get("app"), n), t0=t, t1=t + dur,
                    pos=e.get("pos"), total=e.get("total"))

    def _finish(self, dev, e, n, nxt):
        if e.get("state") == "Playing" and n:
            self._merge(self._seg(dev, e, n, nxt), self.sessions, self.open)

    @staticmethod
    def _merge(s, sessions, open_):
        prev = open_.get((s["dev"], s["key"]))
        if prev and s["t0"] - prev["t1"] <= JOIN:
            prev["t1"] = max(prev["t1"], s["t1"])
            prev["sec"] += s["t1"] - s["t0"]
            prev["pos"], prev["total"] = s["pos"], s["total"]
        else:
            sessions.append(dict(dev=s["dev"], app=s["app"], n=s["n"], key=s["key"], t0=s["t0"], t1=s["t1"],
                                 sec=s["t1"] - s["t0"], pos=s["pos"], total=s["total"]))
            open_[(s["dev"], s["key"])] = sessions[-1]

    def result(self, now: float | None = None):
        """(sessions, last_state) as of `now`, like sessions_from_events(). Doesn't change the builder."""
        now = time.time() if now is None else now
        out = [dict(x) for x in self.sessions]
        idx = {id(x): i for i, x in enumerate(self.sessions)}
        open_ = {k: out[idx[id(v)]] for k, v in self.open.items()}
        last_state = {}
        for dev, (e, n) in self.pending.items():
            t = e["_t"]
            if e.get("state") == "Playing" and n:
                self._merge(self._seg(dev, e, n, min(now, t + CAP)), out, open_)
            if e.get("state") == "Playing" and now - t < CAP:
                # apps that send no metadata (Netflix) still show as playing, just without a title
                nn = n or dict(kind="title", series=e.get("app"), season=None, ep=None, ept=None, ch="",
                               title=e.get("app"))
                last_state[dev] = dict(app=e.get("app"), n=nn, pos=e.get("pos"), total=e.get("total"))
        for x in out:
            lp = self.lastpos.get((x["dev"], x["key"]))
            if lp and lp[0] is not None:
                x["pos"], x["total"] = lp[0], lp[1]
        out.sort(key=lambda x: x["t0"])
        return out, last_state


def _slim(e, t):
    return dict(app=e.get("app"), state=e.get("state"), pos=e.get("pos"), total=e.get("total"), _t=t)


def build(core, now: float | None = None) -> dict:
    """Everything the phone app's main screens need, from the event log. `core` is a strivo.core.Core."""
    now = time.time() if now is None else now
    sessions, last_state, first_ts = core.sessions(now)
    c = core.rules()
    _names = {}

    def name(d):
        if d not in _names:
            _names[d] = core.device_name(d)
        return _names[d]

    def is_movie(x):
        n = x["n"]
        return n["kind"] == "title" and (n["series"] in c["movies"] or (x["total"] or 0) >= c["movie_min_sec"]) \
            and n["series"] not in c["recurring"]

    series = {}
    for x in sessions:
        n = x["n"]
        if n["kind"] not in ("episode", "title") or x["sec"] < 60 or is_movie(x):
            continue
        k = (x["app"], n["series"])
        r = series.setdefault(k, dict(app=x["app"], series=n["series"], eps={}, last=0, sec=0))
        r["sec"] += x["sec"]
        ek = (n["season"], n["ep"], n["ept"])
        e = r["eps"].setdefault(ek, dict(season=n["season"], ep=n["ep"], ept=n["ept"], sec=0, pos=None, total=None,
                                         last=0, done=False))
        e["sec"] += x["sec"]
        if x["t1"] >= e["last"]:
            e["last"], e["pos"], e["total"] = x["t1"], x["pos"], x["total"]
        r["last"] = max(r["last"], x["t1"])
    out_series = []
    for r in series.values():
        eps = sorted(r["eps"].values(), key=lambda e: e["last"])
        for e in eps:
            tot = e["total"] or 0
            e["done"] = bool(tot and e["pos"] is not None and e["pos"] / tot >= 0.9)
        for e in eps:   # moving on to a later numbered episode means this one is finished
            if not e["done"] and e["ep"] is not None and any(
                    o["ep"] is not None and (o["season"] or 0, o["ep"]) > (e["season"] or 0, e["ep"])
                    and o["last"] > e["last"] for o in eps):
                e["done"] = True
        latest = eps[-1]
        out_series.append(dict(app=r["app"], series=r["series"], last=r["last"], hours=round(r["sec"] / 3600, 2),
                               episodes=[dict(e) for e in eps], latest=latest, resumable=not latest["done"]))
    out_series.sort(key=lambda s: -s["last"])

    def fmt(x):
        n = x["n"]
        return dict(dev=name(x["dev"]), app=x["app"], kind=n["kind"], series=n["series"], season=n["season"],
                    ep=n["ep"], ept=n["ept"], ch=n["ch"], t0=x["t0"], t1=x["t1"], min=round(x["sec"] / 60, 1),
                    pos=x["pos"], total=x["total"])

    cutoff = now - c["news_days"] * 86400

    def newsy(x):
        return x["n"]["kind"] in ("live", "title", "episode") and is_news(c, x["n"]["series"], x["n"]["ch"])

    def late(t):
        if any(k in (t or "").lower() for k in c["late_night"]):
            return True
        if ";" in (t or "") or re.search(r": [A-Z][a-z]+ \d{1,2}, \d{4}$", t or ""):
            return True   # late-night episodes titled by guest or by date
        info = core.show_cached(t) if t else None
        return bool(info and 10767 in (info.get("genres") or []))   # TMDB "Talk"

    def tv_news(t):
        info = core.show_cached(t) if t else None
        return bool(info and 10763 in (info.get("genres") or []))   # TMDB "News"

    sessions_out = [fmt(x) for x in sorted(sessions, key=lambda x: -x["t0"])
                    if x["sec"] >= 30 and not (newsy(x) and x["t1"] < cutoff)]
    for sr in out_series:
        eps = sr["episodes"]
        recent = [e for e in eps if e["last"] > now - c["auto_recurring_days"] * 86400]
        if sr["series"] in c["recurring"] or len(recent) >= c["auto_recurring_eps"] or late(sr["series"]):
            sr["cat"] = "recurring"
            sr["episodes"] = eps[-c["recurring_keep"]:]
        else:
            sr["cat"] = "series"
    out_series = [sr for sr in out_series if not is_news(c, sr["series"], "") and not tv_news(sr["series"])]
    # ---- movies
    mv = {}
    for x in sessions:
        if x["sec"] < 60 or not is_movie(x):
            continue
        m = mv.setdefault(x["n"]["series"], dict(title=x["n"]["series"], app=x["app"], sec=0, last=0, pos=None,
                                                   total=None))
        m["sec"] += x["sec"]
        if x["t1"] >= m["last"]:
            m["last"], m["pos"], m["total"], m["app"] = x["t1"], x["pos"], x["total"], x["app"]
    movies = []
    for m in sorted(mv.values(), key=lambda m: -m["last"]):
        tot = m["total"] or 0
        m["done"] = bool(tot and m["pos"] is not None and m["pos"] / tot >= 0.9)
        m["hours"] = round(m["sec"] / 3600, 2)
        movies.append(m)
    # ---- Daily: live shows (news, late night, anything on live TV) and recurring/game shows
    dly = {}
    for x in sessions:
        n = x["n"]
        if n["kind"] != "live" or x["sec"] < 60:
            continue
        news = is_news(c, n["series"], n["ch"])
        if x["t1"] < now - (c["news_days"] if news else c["daily_days"]) * 86400:
            continue
        d = dly.setdefault(n["series"], dict(series=n["series"], app=x["app"], ch=n["ch"],
                                             cat="news" if news else "late" if late(n["series"]) else "live",
                                             episodes=[], last=0, sec=0))
        d["episodes"].append(dict(season=None, ep=None,
                                  ept=datetime.fromtimestamp(x["t0"]).strftime("%a %b %d, %I:%M %p"),
                                  sec=x["sec"], pos=None, total=None, last=x["t1"], done=True))
        d["sec"] += x["sec"]
        d["last"] = max(d["last"], x["t1"])
    for sr in out_series:
        if sr["cat"] == "recurring":
            if sr["last"] < now - c["daily_days"] * 86400:
                continue
            dly[sr["series"]] = dict(series=sr["series"], app=sr["app"], ch="",
                                     cat="late" if late(sr["series"]) else "game", episodes=sr["episodes"],
                                     last=sr["last"], sec=sr["hours"] * 3600, latest=sr["latest"])
    daily = sorted(dly.values(), key=lambda d: -d["last"])
    for d in daily:
        d["hours"] = round(d["sec"] / 3600, 2)
        d["episodes"] = sorted(d["episodes"], key=lambda e: e["last"])
    out_series = [sr for sr in out_series if sr["cat"] != "recurring"]
    series_all = list(out_series)
    # ---- removal from Watching: hidden until watched again for at least restore_min minutes
    dm = core.dismissed()
    removed = []

    def last_watch(nm):
        t = 0
        for x in sessions:
            if x["n"]["series"] == nm and x["sec"] >= c["restore_min"] * 60:
                t = max(t, x["t0"])
        return t

    def keep(kind, nm, sr=None):
        k = kind + ":" + nm
        if k not in dm:
            return True
        if last_watch(nm) > dm[k]:
            core.set_dismissed(k, False)
            return True
        if sr is not None:
            # a new season that premiered after you removed the show brings it back (removing it again hides it
            # until the next season)
            ns = core.season_since(nm, dm[k])
            if ns:
                sr["new_season"] = ns[0]
                return True
        removed.append(dict(kind=kind, name=nm, at=dm[k]))
        return False

    for sr in out_series:
        sr["unwatched"] = core.unwatched(sr)
        sr["new_air"] = core.new_since(sr)
    # Watching is the nighttime queue: shows watched recently, or with an episode that aired since the last watch
    act = now - c["queue_days"] * 86400
    new_cut = time.strftime("%Y-%m-%d", time.localtime(now - c["queue_new_days"] * 86400))
    out_series = [sr for sr in out_series if sr["last"] >= act or
                  (sr["new_air"] and sr["new_air"] >= new_cut and (sr["unwatched"] or 0) > 0)]
    out_series.sort(key=lambda sr: (not (sr["new_air"] and (sr["unwatched"] or 0) > 0), -sr["last"]))
    out_series = [sr for sr in out_series if keep("series", sr["series"], sr)]
    # shows added from What to Watch, not started yet (dropped once an episode is watched)
    seen = {nm_key(sr["series"]) for sr in series_all}
    all_q = core.queue()
    q = [x for x in all_q if nm_key(x["title"]) not in seen]
    if len(q) != len(all_q):
        core.save_queue(q)
    movies = [m for m in movies if keep("movie", m["title"])]
    daily = [d for d in daily if keep("daily", d["series"])]
    hours = defaultdict(float)
    for x in sessions:
        hours[x["app"] or "Unknown"] += x["sec"] / 3600
    nowp = {name(d): dict(app=v["app"], series=v["n"]["series"], title=v["n"]["title"], ept=v["n"]["ept"],
                          ch=v["n"]["ch"], kind=v["n"]["kind"], pos=v["pos"], total=v["total"])
            for d, v in last_state.items()}
    return dict(generated=now, now=nowp, sessions=sessions_out, series=out_series, queued=q, movies=movies,
                daily=daily, removed=removed,
                hours={k: round(v, 2) for k, v in sorted(hours.items(), key=lambda x: -x[1])},
                cfg=dict(recurring_keep=c["recurring_keep"], news_days=c["news_days"], daily_days=c["daily_days"]),
                span_start=first_ts)


def nm_key(x):
    return re.sub(r"[^a-z0-9]", "", (x or "").lower())
