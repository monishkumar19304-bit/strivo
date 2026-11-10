"""What to Watch: movie, series and category picks from what you actually watched.

Signals, strongest first: titles you marked "watched it, liked it"; TMDB recommendations for what you watch most
(hours, decayed by recency); the genres you watch; and optional taste searches from config ("taste").
Everything is filtered to the services you pay for, in your country, and shaped by "not interested because...".
"""
from __future__ import annotations

import time
from collections import defaultdict

from .config import disc_cfg
from .history import nm_key
from .tmdb import IMG

SUG_HALF_LIFE = 60 * 86400
TV2MOVIE_GENRE = {10759: [28, 12], 10765: [878, 14], 10768: [10752], 10762: [10751]}
TV_SKIP = {10763, 10764, 10766, 10767, 10762}     # news, reality, soap, talk, kids: not primetime picks

# Neutral defaults. A household can describe its taste in config.json "taste" (see docs/CONFIG.md):
#   genres: {"Mystery": 1.0, ...} weights that boost a genre
#   story_genres: ["Adventure", ...] if set, Action picks must also have one of these
#   searches: [[reason, "Genre,Genre", ["keyword", ...]], ...] extra discover queries
DEFAULT_TASTE = {"genres": {}, "story_genres": [], "searches": [["a crowd favorite", "", []]]}
DEFAULT_TV_TASTE = [["a crowd favorite", "", []]]


def _movie_genres(ids):
    out = []
    for g in ids:
        out += TV2MOVIE_GENRE.get(g, [g])
    return out


def taste_seeds(d, limit=15):
    """Weighted watch history: hours watched, decayed by recency. Movies and series only."""
    now = time.time()
    seeds = {}
    for s in d["series"] + [x for x in d["daily"] if x.get("cat") == "game"]:
        if s["hours"] < 0.5:
            continue
        seeds[("tv", s["series"])] = s["hours"] * 0.5 ** ((now - s["last"]) / SUG_HALF_LIFE)
    for m in d["movies"]:
        seeds[("movie", m["title"])] = max(m["hours"], 1.0) * 0.5 ** ((now - m["last"]) / SUG_HALF_LIFE) * 1.5
    return sorted(((w, k) for k, w in seeds.items()), reverse=True)[:limit]


def resolve(core, typ, name):
    res = [x for x in core.tmdb.get("/search/multi", query=name, include_adult="false").get("results", [])
           if x.get("media_type") in ("tv", "movie")]
    pref = [x for x in res if x["media_type"] == typ]
    return (pref or res or [None])[0]


def provider_ids(core, mine, typ="movie"):
    from .apps import prov_map
    ids = []
    for x in core.tmdb.get("/watch/providers/" + typ, watch_region=core.country()).get("results", []):
        if prov_map().get(x["provider_name"]) in mine:
            ids.append(x["provider_id"])
    return ids


def _keyword_ids(core, kws):
    ids = []
    for k in kws:
        try:
            ids += [str(r["id"]) for r in core.tmdb.get("/search/keyword", query=k).get("results", [])[:1]]
        except Exception:
            pass
    return ids


def _all_seen(core):
    return core.seen_names()


def suggest_build(core):
    c = core.config()
    mine = core.services()
    d = core.build()
    taste = dict(DEFAULT_TASTE)
    taste.update(c.get("taste") or {})
    dc = disc_cfg(c)["movies"]
    years, min_rating, min_votes = dc["years"], dc["min_rating"], c.get("suggest_min_votes", 300)
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - years * 365.25 * 86400)) if years else "1900-01-01"
    watched = {nm_key(m["title"]) for m in d["movies"]}
    skip = {(w["id"], w["type"]) for w in core.wishlist()}
    dm = core.dismissed()
    cands = {}
    gname = {g["id"]: g["name"] for g in core.tmdb.get("/genre/movie/list").get("genres", [])}
    gid = {n: i for i, n in gname.items()}
    story = {gid[n] for n in taste.get("story_genres", []) if n in gid}
    fbs = [f for k, f in core.feedback().items() if not k.startswith("tv:")]
    pen, hide_seq = defaultdict(int), False
    for f in fbs:
        tg = f.get("tags") or []
        if "genre" in tg:
            for n in f.get("genres", []):
                pen[n] += 1
        if "kids" in tg:
            pen["Animation"] += 1
            pen["Family"] += 1
        if "sequel" in tg:
            hide_seq = True

    def mult(gids):
        m = 1 + sum(taste.get("genres", {}).get(gname.get(g, ""), 0) for g in gids)
        for g in gids:
            m *= max(0.5, 0.85 ** pen.get(gname.get(g, ""), 0))
        return m

    def add(x, weight, why, floor=None):
        floor = min_rating if floor is None else floor
        if x.get("media_type", "movie") != "movie" or not x.get("poster_path"):
            return
        if (x.get("vote_average") or 0) < floor or (x.get("vote_count") or 0) < min_votes:
            return
        if (x.get("release_date") or "") < since:
            return
        gids = x.get("genre_ids") or []
        if story and gid.get("Action") in gids and not (set(gids) & story):
            return
        r = cands.setdefault(x["id"], dict(id=x["id"], type="movie", title=x["title"],
                                           year=(x.get("release_date") or "")[:4],
                                           poster=IMG + "w185" + x["poster_path"], score=0, why=[],
                                           votes=x.get("vote_count") or 0))
        r["score"] += weight * mult(gids) * (1 + max(0, x["vote_average"] - min_rating) / 2)
        if why not in r["why"]:
            r["why"].append(why)

    pids = "|".join(str(i) for i in provider_ids(core, mine))
    if not pids:
        core.data.write("suggestions.json", dict(built=time.time(), items=[], reason="no services"))
        return []
    disc = {"watch_region": core.country(), "with_watch_providers": pids, "with_watch_monetization_types": "flatrate",
            "sort_by": "popularity.desc", "primary_release_date.gte": since, "vote_count.gte": min_votes,
            "vote_average.gte": min_rating}
    for reason, genres, kws, *opt in taste.get("searches", []):
        floor = float(opt[0]) if opt and isinstance(opt[0], (int, float)) else min_rating
        p = dict(disc, **{"vote_average.gte": floor})
        if genres:
            p["with_genres"] = ",".join(str(gid[g]) for g in genres.split(",") if g in gid)
        if kws:
            ids = _keyword_ids(core, kws)
            if not ids:
                continue
            p["with_keywords"] = "|".join(ids)
        for page in (1, 2):
            try:
                res = core.tmdb.get("/discover/movie", page=page, **p).get("results", [])
            except Exception:
                break
            for x in res:
                add(x, 5.0, reason, floor)
    for mid, f in core.feedback().items():
        if f.get("reason") == "liked" and not mid.startswith("tv:"):
            try:
                for x in core.tmdb.get("/movie/%s/recommendations" % mid).get("results", []):
                    add(x, 3.0, "you liked " + f.get("title", "a movie"))
            except Exception:
                pass
    gscore = defaultdict(float)
    for w, (typ, name) in taste_seeds(d):
        try:
            hit = resolve(core, typ, name)
        except Exception:
            continue
        if not hit:
            continue
        if typ == "movie" and hit["media_type"] == "movie":
            for x in core.tmdb.get("/movie/%d/recommendations" % hit["id"]).get("results", []):
                add(x, w, "you watched " + name)
        try:
            info = core.tmdb.get("/%s/%d" % (hit["media_type"], hit["id"]))
        except Exception:
            continue
        for g in _movie_genres([g["id"] for g in info.get("genres", [])][:3]):
            gscore[g] += w
    for g, w in sorted(gscore.items(), key=lambda x: -x[1])[:3]:
        try:
            res = core.tmdb.get("/discover/movie", **dict(disc, with_genres=g)).get("results", [])
        except Exception:
            continue
        for x in res:
            add(x, w * 0.2, "you watch " + gname.get(g, "these").lower())
    out = []
    for r in sorted(cands.values(), key=lambda r: -r["score"])[:120]:
        if nm_key(r["title"]) in watched or (r["id"], "movie") in skip or ("suggest:%d" % r["id"]) in dm:
            continue
        try:
            wh = core.where(r["id"], "movie")
        except Exception:
            continue
        if hide_seq:
            try:
                col = (core.tmdb.get("/movie/%d" % r["id"]).get("belongs_to_collection") or {}).get("name", "")
            except Exception:
                col = ""
            base = nm_key(col.replace(" Collection", "")) if col else ""
            if base and not any(base in w for w in watched):
                continue
        on = sorted(n for n, k in wh.get("providers", {}).items() if k == "flatrate" and n in mine)
        if not on:
            continue
        out.append(dict(r, providers=on, overview=wh.get("overview", "")[:2000], genres=wh.get("genres", []),
                        rating=wh.get("rating"), runtime=wh.get("runtime"), score=round(r["score"], 2), why=r["why"][:3]))
        if len(out) >= 30:
            break
    core.data.write("suggestions.json", dict(built=time.time(), items=out))
    return out


def suggestions(core):
    c = core.data.read("suggestions.json", {"built": 0, "items": []})
    dm = core.dismissed()
    skip = {(w["id"], w["type"]) for w in core.wishlist()}
    items = [i for i in c["items"] if (i["id"], "movie") not in skip and ("suggest:%d" % i["id"]) not in dm]
    gone = [dict(i, at=dm["suggest:%d" % i["id"]]) for i in c["items"] if ("suggest:%d" % i["id"]) in dm]
    return dict(built=c["built"], items=items, dismissed=gone)


def shows_build(core):
    c = core.config()
    mine = core.services()
    d = core.build()
    dc = disc_cfg(c)["series"]
    min_rating, min_votes = dc["min_rating"], c.get("show_min_votes", 150)
    allseen = _all_seen(core) | {nm_key(s["series"]) for s in d["series"]} | {nm_key(s["series"]) for s in d["daily"]}
    skip = {w["id"] for w in core.wishlist() if w["type"] == "tv"} | {q["id"] for q in core.queue()}
    dm = core.dismissed()
    cands = {}

    def add(x, weight, why, floor=None):
        floor = min_rating if floor is None else floor
        if not x.get("poster_path") or not x.get("name"):
            return
        if (x.get("vote_average") or 0) < floor or (x.get("vote_count") or 0) < min_votes:
            return
        if set(x.get("genre_ids") or []) & TV_SKIP:
            return
        r = cands.setdefault(x["id"], dict(id=x["id"], type="tv", title=x["name"],
                                           year=(x.get("first_air_date") or "")[:4],
                                           poster=IMG + "w185" + x["poster_path"], score=0, why=[],
                                           votes=x.get("vote_count") or 0))
        r["score"] += weight * (1 + max(0, x["vote_average"] - min_rating) / 2)
        if why not in r["why"]:
            r["why"].append(why)

    pids = "|".join(str(i) for i in provider_ids(core, mine, "tv"))
    if not pids:
        core.data.write("show_suggestions.json", dict(built=time.time(), items=[], reason="no services"))
        return []
    disc = {"watch_region": core.country(), "with_watch_providers": pids, "with_watch_monetization_types": "flatrate",
            "sort_by": "popularity.desc", "vote_count.gte": min_votes}
    for reason, genres, kws, *opt in (c.get("tv_taste") or DEFAULT_TV_TASTE):
        floor = float(opt[0]) if opt and isinstance(opt[0], (int, float)) else min_rating
        p = dict(disc, **{"vote_average.gte": floor})
        if genres:
            p["with_genres"] = genres
        if kws:
            ids = _keyword_ids(core, kws)
            if not ids:
                continue
            p["with_keywords"] = "|".join(ids)
        for page in (1, 2):
            try:
                res = core.tmdb.get("/discover/tv", page=page, **p).get("results", [])
            except Exception:
                break
            for x in res:
                add(x, 4.0, reason, floor)
    for w, (typ, name) in taste_seeds(d):
        if typ != "tv":
            continue
        try:
            hit = resolve(core, "tv", name)
        except Exception:
            continue
        if not hit or hit.get("media_type") != "tv":
            continue
        try:
            for x in core.tmdb.get("/tv/%d/recommendations" % hit["id"]).get("results", []):
                add(x, w * 2, "you watch " + name)
        except Exception:
            pass
    old = core.data.read("show_suggestions.json", {}).get("items", []) if core.data.read("show_suggestions.json", {}) else []
    have = {i["id"] for i in old}
    out = [i for i in old if ("showsug:%d" % i["id"]) not in dm and i["id"] not in skip and nm_key(i["title"]) not in allseen]
    for r in sorted(cands.values(), key=lambda r: -r["score"])[:100]:
        if len(out) >= 60:
            break
        if r["id"] in have or nm_key(r["title"]) in allseen or r["id"] in skip or ("showsug:%d" % r["id"]) in dm:
            continue
        try:
            wh = core.where(r["id"], "tv")
        except Exception:
            continue
        on = sorted(n for n, k in wh.get("providers", {}).items() if k == "flatrate" and n in mine)
        if not on:
            continue
        out.append(dict(r, providers=on, overview=wh.get("overview", "")[:2000], genres=wh.get("genres", []),
                        rating=wh.get("rating"), seasons=wh.get("seasons"), episodes=wh.get("episodes"),
                        status=wh.get("status", ""), score=round(r["score"], 2), why=r["why"][:3], added=time.time()))
    core.data.write("show_suggestions.json", dict(built=time.time(), items=out))
    return out


def shows(core):
    c = core.data.read("show_suggestions.json", {"built": 0, "items": []})
    dm, lt, now = core.dismissed(), core.later(), time.time()
    skip = {w["id"] for w in core.wishlist() if w["type"] == "tv"} | {q["id"] for q in core.queue()}
    items = [dict(i, later=bool(lt.get(str(i["id"]), 0) > now - 30 * 86400)) for i in c["items"]
             if i["id"] not in skip and ("showsug:%d" % i["id"]) not in dm]
    items.sort(key=lambda i: (i["later"], -i.get("score", 0)))
    gone = [dict(i, at=dm["showsug:%d" % i["id"]]) for i in c["items"] if ("showsug:%d" % i["id"]) in dm]
    return dict(built=c["built"], items=items, dismissed=gone)


def disc_file(cat):
    return "disc_%s.json" % cat


def disc_build(core, cat):
    c = core.config()
    dc = disc_cfg(c)[cat]
    mine = core.services()
    allseen = _all_seen(core)
    skip = {(w["id"], w["type"]) for w in core.wishlist()} | {(q["id"], "tv") for q in core.queue()}
    dm = core.dismissed()
    cands = {}
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - dc["years"] * 365.25 * 86400)) if dc["years"] else ""
    for typ, extra in dc["spec"].items():
        pids = "|".join(str(i) for i in provider_ids(core, mine, typ))
        if not pids:
            continue
        p = {"watch_region": core.country(), "with_watch_providers": pids, "with_watch_monetization_types": "flatrate",
             "sort_by": "popularity.desc", "vote_count.gte": 50 if typ == "tv" else 100,
             "vote_average.gte": dc["min_rating"]}
        for k, v in extra.items():
            if k == "keyword":
                try:
                    p["with_keywords"] = str(core.tmdb.get("/search/keyword", query=v)["results"][0]["id"])
                except Exception:
                    continue
            else:
                p[k] = v
        if since:
            p["first_air_date.gte" if typ == "tv" else "primary_release_date.gte"] = since
        for page in (1, 2, 3):
            try:
                res = core.tmdb.get("/discover/" + typ, page=page, **p).get("results", [])
            except Exception:
                break
            for i, x in enumerate(res):
                name = x.get("name") or x.get("title")
                if not x.get("poster_path") or not name:
                    continue
                cands.setdefault((typ, x["id"]), dict(
                    id=x["id"], type=typ, cat=cat, title=name,
                    year=(x.get("first_air_date") or x.get("release_date") or "")[:4],
                    poster=IMG + "w185" + x["poster_path"], votes=x.get("vote_count") or 0,
                    score=round((x.get("vote_average") or 0) * 10 - (page - 1) * 5 - i * 0.2, 2),
                    why=["popular " + dc["label"].lower()]))
    old = (core.data.read(disc_file(cat), {}) or {}).get("items", [])

    def ok(r):
        return ("disc:%s:%d" % (r["type"], r["id"])) not in dm and (r["id"], r["type"]) not in skip \
            and nm_key(r["title"]) not in allseen

    out = [r for r in old if ok(r)]
    have = {(r["type"], r["id"]) for r in out}
    for r in sorted(cands.values(), key=lambda r: -r["score"]):
        if len(out) >= 50:
            break
        if (r["type"], r["id"]) in have or not ok(r):
            continue
        try:
            wh = core.where(r["id"], r["type"])
        except Exception:
            continue
        on = sorted(n for n, k in wh.get("providers", {}).items() if k == "flatrate" and n in mine)
        if not on:
            continue
        out.append(dict(r, providers=on, overview=wh.get("overview", "")[:2000], genres=wh.get("genres", []),
                        rating=wh.get("rating"), runtime=wh.get("runtime"), seasons=wh.get("seasons"), added=time.time()))
    core.data.write(disc_file(cat), dict(built=time.time(), items=out))
    return out


def disc_list(core, cat):
    c = core.data.read(disc_file(cat), {"built": 0, "items": []})
    dm, lt, now = core.dismissed(), core.later(), time.time()
    skip = {(w["id"], w["type"]) for w in core.wishlist()} | {(q["id"], "tv") for q in core.queue()}

    def key(i):
        return "%s:%d" % (i["type"], i["id"])

    items = [dict(i, later=bool(lt.get(key(i), 0) > now - 30 * 86400)) for i in c["items"]
             if (i["id"], i["type"]) not in skip and ("disc:" + key(i)) not in dm]
    items.sort(key=lambda i: i["later"])
    gone = [dict(i, at=dm["disc:" + key(i)]) for i in c["items"] if ("disc:" + key(i)) in dm]
    return dict(built=c["built"], items=items, dismissed=gone)


def build_all(core, only=None):
    if not core.tmdb.configured:
        return
    for cat, d in disc_cfg(core.config()).items():
        if only and cat != only:
            continue
        if not d["on"] and not only:
            continue
        try:
            if cat == "movies":
                suggest_build(core)
            elif cat == "series":
                shows_build(core)
            else:
                disc_build(core, cat)
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception("building %s picks failed", cat)
