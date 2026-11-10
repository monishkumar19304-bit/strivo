"""Core: the library (TMDB matching and caching), your lists, settings and notifications.

`Core` is the one object the web server, the CLI and the tests share. It holds no global state: give it a
data folder (and, in tests, a fake TMDB transport) and everything else follows.
"""
from __future__ import annotations

import json
import logging
import os
import copy
import threading
import time
import urllib.parse
import urllib.request

from . import history
from . import rules as rules_mod
from .apps import prov_map, streaming
from .collector import EventLog
from .config import DISC_DEFAULT, DataDir, disc_cfg
from .history import nm_key
from .net import ssl_context
from .tmdb import IMG, TMDB, NoKey

log = logging.getLogger(__name__)

CACHE = "tmdb_cache.json"
REASONS = {"liked": "Watched it, liked it", "disliked": "Watched it, didn't like it",
           "want": "Haven't seen it, want to watch it", "no": "Not interested"}
TAGS = ("genre", "dark", "kids", "sequel", "bad", "other")
SET_RULES = {"queue_days": (int, 7, 60), "queue_new_days": (int, 14, 180), "daily_days": (int, 1, 30),
             "news_days": (int, 1, 14)}


def _best(res, q, key):
    """Prefer an exact title match; otherwise TMDB's own order (relevance)."""
    ex = [x for x in res if nm_key(x.get(key)) == nm_key(q)]
    return (ex or res or [None])[0]


class Core:
    def __init__(self, data: DataDir | str, transport=None, names=None):
        self.data = data if isinstance(data, DataDir) else DataDir(data)
        self.log = EventLog(self.data)
        self._sb, self._sb_rules, self._ev_last, self._ev_lock = None, None, 0, threading.Lock()
        self._memos, self._memo_lock = {}, threading.Lock()
        self.tmdb = TMDB(lambda: self.data.secret("tmdb_key"), transport,
                         lambda: self.config().get("language", "en-US"))
        self.lock = self.data.lock
        self._inflight: set[str] = set()
        self._extras: dict = {}
        self._names = names  # optional callable(dev_id) -> name (the collector's)

    # ---- config helpers
    def config(self) -> dict:
        return copy.deepcopy(self._memo("config.json", self.data.config))

    def rules(self) -> dict:
        return self.config()["rules"]

    def country(self) -> str:
        return (self.config().get("country") or "US").upper()

    def services(self) -> set[str]:
        return set(self.config().get("services") or [])

    def events(self) -> list[dict]:
        """Every event, oldest first, read from the database (not kept in memory: use sessions() for screens)."""
        return self.log.read()

    def _sync(self):
        """Bring the session builder up to date with the history: new rows only, or a full rebuild when the rules
        changed or an event arrived out of order. Returns the id of the newest row seen."""
        rv = rules_mod.current().version
        with self._ev_lock:
            sb = self._sb
            if sb is not None and self._sb_rules == rv:
                new = self.log.read_since(self._ev_last)
                ok = True
                for i, e in new:
                    if not sb.feed(e):
                        ok = False
                        break
                    self._ev_last = i
                if ok:
                    return self._ev_last
            self.log.store.import_legacy()
            last = self.log.store.max_id()
            sb = history.SessionBuilder()
            for i, e in self.log.store.iter_plays_by_device(last):
                sb.feed(e)
            self._sb, self._sb_rules, self._ev_last = sb, rv, last   # rows after `last` come in via read_since
            return self._ev_last

    def sessions(self, now: float | None = None):
        """(sessions, last_state, first event time) for the history screens."""
        self._sync()
        sess, last_state = self._sb.result(now)
        return sess, last_state, self._sb.first_ts

    def seen_names(self) -> set:
        """nm_key of every series and title ever played."""
        self._sync()
        return set(self._sb.seen)

    def device_name(self, dev_id: str) -> str:
        if self._names:
            return self._names(dev_id)
        for d in self.config().get("devices", []):
            if d.get("id") == dev_id:
                return d.get("name") or dev_id
        return (self.data.read("device_names.json", {}) or {}).get(dev_id, dev_id)

    RECENT_DAYS = 90       # /api/data carries this much history; older pages come from history_page()

    def build(self, now: float | None = None) -> dict:
        """The main screens. Sessions older than RECENT_DAYS are left out (years of history would be megabytes on
        every refresh); `sessions_more` says how many, and history_page() serves them."""
        fixed_now = now is not None
        now = time.time() if now is None else now
        self._sync()                               # pick up new rows so the reuse check below sees them
        stamp = (self._ev_last, tuple(self._stamp(f) for f in self.BUILD_INPUTS), rules_mod.current().version,
                 int(now // 60))
        hit = getattr(self, "_built", None)
        if not fixed_now and hit and hit[0] == stamp:
            return copy.deepcopy(hit[1])           # nothing changed this minute: phones polling cost almost nothing
        full = self._build_full(now)
        if not fixed_now:
            self._built = (stamp, copy.deepcopy(full))
        return full

    BUILD_INPUTS = ("config.json", "dismissed.json", "tmdb_cache.json", "queue.json", "later.json", "wishlist.json",
                    "secrets.json", "device_names.json")

    def _stamp(self, name):
        try:
            st = os.stat(self.data.file(name))
            return (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            return None

    def _build_full(self, now: float) -> dict:
        full = history.build(self, now)
        allsess = full["sessions"]                 # newest first
        self._sessions = allsess
        cut = now - self.RECENT_DAYS * 86400
        recent = [x for x in allsess if x["t0"] >= cut]
        full["sessions"] = recent
        full["sessions_more"] = len(allsess) - len(recent)
        return full

    def history_page(self, before: float, limit: int = 400) -> dict:
        """Sessions that started before `before`, newest first: the History page's "Show older"."""
        allsess = getattr(self, "_sessions", None)
        if allsess is None:
            self.build()
            allsess = self._sessions
        limit = max(1, min(int(limit), 2000))
        older = [x for x in allsess if x["t0"] < before]
        return dict(sessions=older[:limit], more=max(0, len(older) - limit))

    # ---- TMDB cache
    def _cache(self) -> dict:
        """The TMDB cache, parsed once per change of the file (it's read dozens of times per screen). Read-only."""
        return self._memo(CACHE, lambda: self.data.read(CACHE, {}) or {})

    def _memo(self, name, load):
        try:
            st = os.stat(self.data.file(name))
            stamp = (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            stamp = None
        with self._memo_lock:
            hit = self._memos.get(name)
            if hit and hit[0] == stamp and stamp is not None:
                return hit[1]
        val = load()
        with self._memo_lock:
            self._memos[name] = (stamp, val)
        return val

    def _cache_put(self, k, v):
        def fn(c):
            c = c or {}
            c[k] = v
            return c
        self.data.update(CACHE, fn, {})

    def poster(self, q: str, typ: str = "") -> str | None:
        """Poster URL for a title. typ "tv" or "movie" searches only that kind."""
        ck = "poster2:%s:%s" % (typ, q)
        cache = self._cache()
        if ck in cache:
            return cache[ck]
        if not self.tmdb.configured:
            return None
        try:
            if typ == "tv":
                sh = cache.get("show:" + q) or {}
                hit = _best([x for x in self.tmdb.get("/search/tv", query=q).get("results", []) if x.get("poster_path")],
                            q, "name")
                url = (IMG + "w342" + hit["poster_path"]) if hit else (sh.get("poster") or "")
            elif typ == "movie":
                hit = _best([x for x in self.tmdb.get("/search/movie", query=q).get("results", [])
                             if x.get("poster_path")], q, "title")
                url = (IMG + "w342" + hit["poster_path"]) if hit else ""
            else:
                res = [x for x in self.tmdb.get("/search/multi", query=q).get("results", [])
                       if x.get("poster_path") and x.get("media_type") in ("tv", "movie")]
                ex = [x for x in res if nm_key(x.get("name") or x.get("title")) == nm_key(q)]
                hit = (ex or res or [None])[0]
                url = (IMG + "w342" + hit["poster_path"]) if hit else ""
        except Exception:
            return None
        self._cache_put(ck, url)
        return url

    def show(self, name: str, force: bool = False):
        """Full episode list for a series from TMDB (cached)."""
        ck = "show:" + name
        cache = self._cache()
        if ck in cache and not force:
            return cache[ck]
        out = {"id": None, "seasons": []}
        try:
            res = self.tmdb.get("/search/tv", query=name).get("results", [])
            if res:
                sid = _best(res, name, "name")["id"]
                info = self.tmdb.get("/tv/" + str(sid))
                out = {"id": sid, "name": info.get("name"), "overview": info.get("overview"),
                       "genres": [g["id"] for g in info.get("genres", [])],
                       "poster": (IMG + "w342" + info["poster_path"]) if info.get("poster_path") else "",
                       "seasons": []}
                for sn in info.get("seasons", []):
                    if sn["season_number"] == 0:
                        continue
                    d = self.tmdb.get("/tv/%d/season/%d" % (sid, sn["season_number"]))
                    out["seasons"].append({"n": sn["season_number"], "episodes": [
                        {"ep": e["episode_number"], "name": e.get("name"), "air": e.get("air_date")}
                        for e in d.get("episodes", [])]})
        except NoKey:
            return None
        except Exception:
            return None
        out["fetched"] = time.time()
        self._cache_put(ck, out)
        return out

    def show_cached(self, name: str, background: bool = True):
        """Cached TMDB show; refreshed in the background when missing or older than 12 h."""
        e = self._cache().get("show:" + name)
        if (not e or time.time() - e.get("fetched", 0) > 12 * 3600) and name not in self._inflight \
                and self.tmdb.configured and background:
            self._inflight.add(name)

            def go():
                try:
                    self.show(name, force=True)
                finally:
                    self._inflight.discard(name)
            threading.Thread(target=go, daemon=True).start()
        return e

    def unwatched(self, sr):
        """Aired episodes after the latest one you watched. None when the show can't be matched."""
        info = self.show_cached(sr["series"])
        if not info or not info.get("seasons"):
            return None
        today = time.strftime("%Y-%m-%d")
        order, hit = [], None
        for sn in info["seasons"]:
            for e in sn["episodes"]:
                if e.get("air") and e["air"] <= today:
                    order.append((sn["n"], e["ep"], e.get("name")))
        for i, (sn, ep, name) in enumerate(order):
            for w in sr["episodes"]:
                if (w["season"] is not None and w["ep"] is not None and w["season"] == sn and w["ep"] == ep) or \
                        (w["ept"] and nm_key(w["ept"]) == nm_key(name)):
                    hit = i if hit is None or i > hit else hit
        return None if hit is None else len(order) - hit - 1

    def new_since(self, sr):
        """Air date of the newest aired episode if it aired after your last watch, else None."""
        info = self.show_cached(sr["series"])
        if not info or not info.get("seasons"):
            return None
        today = time.strftime("%Y-%m-%d")
        lastday = time.strftime("%Y-%m-%d", time.localtime(sr["last"]))
        airs = [e["air"] for sn in info["seasons"] for e in sn["episodes"] if e.get("air") and e["air"] <= today]
        newest = max(airs, default=None)
        return newest if newest and newest > lastday else None

    def season_since(self, name: str, since: float):
        """(season number, premiere date) of the newest season that premiered after `since` and has aired, or None.

        Used to bring a show you removed from Watching back when a new season starts."""
        info = self.show_cached(name)
        if not info or not info.get("seasons"):
            return None
        today = time.strftime("%Y-%m-%d")
        sinceday = time.strftime("%Y-%m-%d", time.localtime(since))
        best = None
        for sn in info["seasons"]:
            airs = [e["air"] for e in sn["episodes"] if e.get("air")]
            if not airs:
                continue
            premiere = min(airs)
            if sinceday < premiere <= today and (best is None or sn["n"] > best[0]):
                best = (sn["n"], premiere)
        return best

    # ---- simple stores
    def queue(self):
        return self.data.read("queue.json", []) or []

    def save_queue(self, q):
        self.data.write("queue.json", q, indent=1)

    def dismissed(self):
        return self.data.read("dismissed.json", {}) or {}

    def set_dismissed(self, key, on):
        def fn(d):
            d = d or {}
            if on:
                d[key] = time.time()
            else:
                d.pop(key, None)
            return d
        self.data.update("dismissed.json", fn, {})

    def wishlist(self):
        return self.data.read("wishlist.json", []) or []

    def save_wishlist(self, w):
        self.data.write("wishlist.json", w, indent=1)

    def later(self):
        return self.data.read("later.json", {}) or {}

    def set_later(self, sid, on):
        def fn(lt):
            lt = lt or {}
            if on:
                lt[str(sid)] = time.time()
            else:
                lt.pop(str(sid), None)
            return lt
        self.data.update("later.json", fn, {})

    def feedback(self):
        return self.data.read("feedback.json", {}) or {}

    def save_feedback(self, mid, title, reason, note, tags=(), genres=(), kind="movie"):
        def fn(fb):
            fb = fb or {}
            fb[("tv:%d" % mid) if kind == "tv" else str(mid)] = dict(
                title=title, reason=reason, note=(note or "")[:300], tags=[t for t in tags if t in TAGS][:6],
                genres=[str(g)[:30] for g in genres][:6], at=time.time())
            return fb
        self.data.update("feedback.json", fn, {}, indent=1)

    # ---- search, where to watch, extras
    def search(self, q):
        r = self.tmdb.get("/search/multi", query=q, include_adult="false").get("results", [])
        out = []
        for x in r:
            if x.get("media_type") not in ("tv", "movie"):
                continue
            out.append(dict(id=x["id"], type=x["media_type"], title=x.get("title") or x.get("name"),
                            year=(x.get("release_date") or x.get("first_air_date") or "")[:4],
                            poster=(IMG + "w185" + x["poster_path"]) if x.get("poster_path") else "",
                            overview=(x.get("overview") or "")[:200]))
        return out[:12]

    def where(self, wid, typ, force=False):
        """Providers in your country plus air info, cached 12 h."""
        country = self.country()
        ck = "where:%s:%s:%s" % (country, typ, wid)
        e = self._cache().get(ck)
        if e and not force and e.get("v") == 3 and time.time() - e.get("fetched", 0) < 12 * 3600:
            return e
        pr = self.tmdb.get("/%s/%s/watch/providers" % (typ, wid)).get("results", {}).get(country, {})
        prov = {}
        for kind in ("flatrate", "free", "ads", "rent", "buy"):
            for x in pr.get(kind, []):
                prov.setdefault(prov_map().get(x["provider_name"], x["provider_name"]), kind)
        info = self.tmdb.get("/%s/%s" % (typ, wid), append_to_response="credits")
        cr = info.get("credits") or {}
        people = [x["name"] for x in (info.get("created_by") or [])] if typ == "tv" else \
            [x["name"] for x in cr.get("crew", []) if x.get("job") == "Director"]
        out = dict(v=3, tagline=info.get("tagline") or "", cast=[x["name"] for x in cr.get("cast", [])[:8]],
                   makers=people[:4], homepage=info.get("homepage") or "", providers=prov, link=pr.get("link", ""),
                   status=info.get("status", ""), fetched=time.time(), overview=(info.get("overview") or "")[:2000],
                   genres=[g["name"] for g in info.get("genres", [])][:6], rating=round(info.get("vote_average") or 0, 1))
        if typ == "tv":
            ne = info.get("next_episode_to_air") or {}
            nets = [n["name"] for n in info.get("networks", [])]
            out.update(networks=nets, first_air=info.get("first_air_date"), seasons=info.get("number_of_seasons"),
                       episodes=info.get("number_of_episodes"), network=nets[0] if nets else "",
                       next_air=ne.get("air_date"),
                       next_ep=("S%s E%s" % (ne.get("season_number"), ne.get("episode_number"))) if ne else "",
                       live=any(n in self.rules()["live_networks"] for n in nets), last_air=info.get("last_air_date"),
                       ended=info.get("status") in ("Ended", "Canceled"))
        else:
            out.update(release=info.get("release_date"), runtime=info.get("runtime"),
                       studios=[x["name"] for x in info.get("production_companies", [])][:2])
        self._cache_put(ck, out)
        return out

    def wishlist_view(self):
        mine = self.services()
        out = []
        for w in self.wishlist():
            try:
                wh = self.where(w["id"], w["type"])
            except Exception:
                wh = {}
            prov = wh.get("providers", {})
            out.append(dict(w, providers=[dict(name=n, kind=k, mine=n in mine) for n, k in
                                          sorted(prov.items(), key=lambda x: (x[0] not in mine, x[0]))],
                            **{k: wh.get(k) for k in ("status", "network", "next_air", "next_ep", "live", "release",
                                                      "link", "first_air", "seasons", "episodes", "overview",
                                                      "tagline", "last_air", "homepage", "rating", "runtime")},
                            networks=wh.get("networks") or [], cast=wh.get("cast") or [],
                            makers=wh.get("makers") or [], genres=wh.get("genres") or [],
                            studios=wh.get("studios") or []))
        return out

    def notify(self, title, message):
        """Optional: POST {title, message} to the notify URL from Settings (ntfy, Home Assistant, Gotify...)."""
        url = self.config().get("notify_url")
        if not url:
            return False
        try:
            urllib.request.urlopen(urllib.request.Request(
                url, data=json.dumps({"title": title, "message": message}).encode(),
                headers={"Content-Type": "application/json", "Title": title}), timeout=15,
                context=ssl_context() if url.startswith("https") else None)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("notify failed: %s", e)
            return False

    def wishlist_check(self):
        """Every few hours: tell you when something lands on one of your services or airs soon."""
        mine = self.services()
        today = time.strftime("%Y-%m-%d")
        tomorrow = time.strftime("%Y-%m-%d", time.localtime(time.time() + 86400))
        items = self.wishlist()
        for w in items:
            try:
                wh = self.where(w["id"], w["type"], force=True)
            except Exception:
                continue
            now_mine = sorted(n for n in wh.get("providers", {}) if n in mine)
            new = [n for n in now_mine if n not in w.get("seen_providers", [])]
            if new and w.get("primed"):
                self.notify("On your wishlist: " + w["title"], "Now available on " + ", ".join(new) + ".")
            w["seen_providers"] = now_mine
            w["primed"] = True
            na = wh.get("next_air")
            if na in (today, tomorrow) and w.get("notified_air") != na:
                when = "tonight" if na == today else "tomorrow"
                self.notify("On your wishlist: " + w["title"], "%s %sairs %s." % (
                    wh.get("next_ep", ""), ("on " + wh["network"] + " ") if wh.get("network") else "", when))
                w["notified_air"] = na
        if items:
            self.save_wishlist(items)

    def extras(self, mid, typ="movie"):
        """Trailer, reviews and review links for the detail view (cached 12 h in memory)."""
        e = self._extras.get((typ, mid))
        if e and time.time() - e["t"] < 12 * 3600:
            return e["v"]
        info = self.tmdb.get("/%s/%d" % (typ, mid), append_to_response="videos,reviews,external_ids")
        vids = [v for v in (info.get("videos") or {}).get("results", []) if v.get("site") == "YouTube"]
        vids.sort(key=lambda v: (v.get("type") != "Trailer", not v.get("official")))
        tr = next((v for v in vids if v.get("type") == "Trailer"), vids[0] if vids else None)
        revs = [dict(author=r.get("author", ""), rating=(r.get("author_details") or {}).get("rating"),
                     text=(r.get("content") or "")[:700], url=r.get("url", ""), date=(r.get("created_at") or "")[:10])
                for r in (info.get("reviews") or {}).get("results", [])[:5]]
        t = info.get("title") or info.get("name") or ""
        q = urllib.parse.quote(t)
        imdb = (info.get("external_ids") or {}).get("imdb_id")
        links = [("TMDB reviews", "https://www.themoviedb.org/%s/%d/reviews" % (typ, mid))]
        if imdb:
            links.insert(0, ("IMDb", "https://www.imdb.com/title/%s/reviews" % imdb))
        links += [("Rotten Tomatoes", "https://www.rottentomatoes.com/search?search=" + q),
                  ("Metacritic", "https://www.metacritic.com/search/%s/" % q)]
        if typ == "movie":
            links.append(("Letterboxd", "https://letterboxd.com/tmdb/%d/" % mid))
        v = dict(trailer=tr["key"] if tr else "", reviews=revs, links=[dict(name=a, url=b) for a, b in links],
                 tmdb_rating=round(info.get("vote_average") or 0, 1), votes=info.get("vote_count") or 0)
        self._extras[(typ, mid)] = dict(t=time.time(), v=v)
        return v

    # ---- settings (shared by the household)
    def settings_view(self):
        c = self.config()
        r = c["rules"]
        return dict(my_services=c.get("services") or [], all_services=sorted(set(streaming()) | set(c.get("services") or [])),
                    default_tv=c.get("default_tv", ""), discover=disc_cfg(c), country=c.get("country", "US"),
                    notify_url=c.get("notify_url", ""), tmdb_key_set=self.tmdb.configured,
                    rules_updates=c.get("rules_updates", "auto"), update_checks=c.get("update_checks", True),
                    **{k: r[k] for k in SET_RULES})

    def settings_save(self, body: dict):
        def fn(c):
            r = c.setdefault("rules", {})
            for k, (typ, lo, hi) in SET_RULES.items():
                if k in body:
                    r[k] = max(lo, min(hi, typ(body[k])))
            if "my_services" in body:
                allowed = set(streaming()) | set(prov_map().values())
                c["services"] = [str(x) for x in body["my_services"] if str(x) in allowed]
            if "default_tv" in body:
                c["default_tv"] = str(body["default_tv"])[:60]
            if "country" in body and str(body["country"]).isalpha() and len(str(body["country"])) == 2:
                c["country"] = str(body["country"]).upper()
            if "notify_url" in body:
                u = str(body["notify_url"]).strip()[:300]
                c["notify_url"] = u if (u == "" or u.startswith(("http://", "https://"))) else c.get("notify_url", "")
            if body.get("rules_updates") in ("auto", "ask", "off"):
                c["rules_updates"] = body["rules_updates"]
            if "update_checks" in body:
                c["update_checks"] = bool(body["update_checks"])
            if isinstance(body.get("discover"), dict):
                d = c.setdefault("discover", {})
                for cat, v in body["discover"].items():
                    if cat not in DISC_DEFAULT or not isinstance(v, dict):
                        continue
                    e = d.setdefault(cat, {})
                    if "on" in v:
                        e["on"] = bool(v["on"])
                    if "min_rating" in v:
                        e["min_rating"] = max(5.0, min(9.0, float(v["min_rating"])))
                    if "years" in v:
                        e["years"] = max(0, min(50, int(v["years"])))
        self.data.update_config(fn)
        return self.settings_view()
