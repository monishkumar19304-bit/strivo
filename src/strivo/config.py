"""One data folder holds everything Strivo knows: config, secrets, history, caches.

Layout (all plain files, so a backup is just a copy of the folder):

    config.json        settings from the setup wizard and the Settings page
    secrets.json       TMDB key and media-server tokens (mode 0600, never served)
    events.jsonl       raw "now playing" moments from every box (the history)
    pyatv.json         Apple TV pairing credentials (pyatv storage)
    adbkey, adbkey.pub this Strivo's ADB identity for Google TV / Android TV / Fire TV
    *.json             queue, wishlist, picks, feedback and the TMDB cache
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from pathlib import Path

ENV_DATA = "STRIVO_DATA"


def default_data_dir() -> Path:
    if os.environ.get(ENV_DATA):
        return Path(os.environ[ENV_DATA]).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "strivo"


# Rules that shape the queue. Everything here is a heuristic about kinds of TV, never about one household.
DEFAULT_RULES = {
    "recurring_keep": 5,          # latest N episodes kept for game shows and daily shows
    "recurring": [],              # titles you want treated as daily/game shows
    "auto_recurring_eps": 4,      # a series with this many episodes inside auto_recurring_days counts as recurring
    "auto_recurring_days": 3,
    "movie_min_sec": 4500,        # untagged titles at least this long (75 min) are treated as movies
    "movies": [],                 # titles to force into Movies
    "daily_days": 7,              # late-night, game and other daily shows are kept this many days
    "queue_days": 21,             # a series stays in Watching this long after the last episode watched
    "queue_new_days": 60,         # ...or while an episode that aired after your last watch is this recent
    "late_night": ["jimmy kimmel", "late show", "tonight show", "late night", "daily show", "seth meyers",
                   "last week tonight", "the view", "today show", "good morning america"],
    "restore_min": 5,             # watching a removed show again for this many minutes puts it back
    "live_networks": ["NBC", "ABC", "CBS", "FOX", "CNN", "MSNBC", "ESPN", "TNT", "TBS", "USA Network", "FX", "AMC",
                      "Comedy Central", "HBO", "Showtime", "Bravo", "The CW", "PBS", "Food Network", "HGTV", "Discovery"],
    "news_days": 2,               # news broadcasts are only kept this many days
    "news_channels": ["CNN", "FOX News", "Fox Business", "MSNBC", "MS NOW", "NewsNation", "CNBC", "Bloomberg",
                      "The Weather Channel", "BBC News", "Sky News"],
    "news_titles": ["news", "nightly", "meet the press", "face the nation", "the weather channel"],
}

DISC_DEFAULT = {
    "series":  dict(label="Series", on=True, min_rating=7.5, years=0),
    "movies":  dict(label="Movies", on=True, min_rating=7.5, years=3),
    "docs":    dict(label="Documentaries", on=True, min_rating=7.2, years=0,
                    spec={"tv": {"with_genres": "99"}, "movie": {"with_genres": "99"}}),
    "reality": dict(label="Reality", on=False, min_rating=7.0, years=0, spec={"tv": {"with_genres": "10764"}}),
    "kids":    dict(label="Kids & Family", on=False, min_rating=7.0, years=0,
                    spec={"tv": {"with_genres": "10762"}, "movie": {"with_genres": "10751"}}),
    "standup": dict(label="Stand-up", on=False, min_rating=7.0, years=0, spec={"movie": {"keyword": "stand-up comedy"}}),
    "anim":    dict(label="Animation", on=False, min_rating=7.5, years=0,
                    spec={"tv": {"with_genres": "16"}, "movie": {"with_genres": "16"}}),
}

DEFAULT_CONFIG = {
    "version": 1,
    "setup_done": False,
    "country": "US",
    "language": "en-US",
    "services": [],               # streaming services you pay for (names from strivo.apps)
    "devices": [],                # [{id, kind, name, host, port, identifier, model, enabled}]
    "default_tv": "",
    "notify_url": "",             # optional: POST JSON here for wishlist alerts (e.g. an ntfy or Home Assistant webhook)
    "poll_seconds": 10,           # how often polled adapters (Android, Roku, Jellyfin, Plex) are asked what's playing
    "rules": DEFAULT_RULES,
    "discover": {},               # per-category overrides of DISC_DEFAULT (on, min_rating, years)
    "taste": {},                  # optional overrides for picks (see strivo.picks.DEFAULT_TASTE)
}


class DataDir:
    """Thread-safe JSON files in one folder."""

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path).expanduser()
        self.path.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def file(self, name: str) -> Path:
        return self.path / name

    def read(self, name: str, default=None):
        try:
            with open(self.file(name)) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return copy.deepcopy(default)

    def write(self, name: str, data, private: bool = False, indent=None):
        with self.lock:
            fd, tmp = tempfile.mkstemp(dir=self.path, prefix="." + name, suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f, indent=indent, default=str)
                if private:
                    os.chmod(tmp, 0o600)
                os.replace(tmp, self.file(name))
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

    def update(self, name: str, fn, default=None, **kw):
        """Read-modify-write under the lock. fn gets the current value and returns the new one."""
        with self.lock:
            new = fn(self.read(name, default))
            self.write(name, new, **kw)
            return new

    # ---- config
    def config(self) -> dict:
        c = copy.deepcopy(DEFAULT_CONFIG)
        user = self.read("config.json", {}) or {}
        rules = dict(DEFAULT_RULES)
        rules.update(user.get("rules") or {})
        c.update(user)
        c["rules"] = rules
        return c

    def save_config(self, c: dict):
        self.write("config.json", c, indent=2)

    def update_config(self, fn):
        with self.lock:
            c = self.config()
            fn(c)
            self.save_config(c)
            return c

    # ---- secrets (TMDB key, server tokens). Never returned by the web API.
    def secrets(self) -> dict:
        return self.read("secrets.json", {}) or {}

    def set_secret(self, key: str, value: str | None):
        def fn(s):
            s = s or {}
            if value:
                s[key] = value
            else:
                s.pop(key, None)
            return s
        self.update("secrets.json", fn, {}, private=True)

    def secret(self, key: str) -> str:
        v = self.secrets().get(key)
        if not v and key == "tmdb_key":
            v = os.environ.get("STRIVO_TMDB_KEY", "")
        return v or ""


def disc_cfg(c: dict) -> dict:
    user = c.get("discover") or {}
    out = {}
    for k, d in DISC_DEFAULT.items():
        e = copy.deepcopy(d)
        e.update({kk: vv for kk, vv in (user.get(k) or {}).items() if kk in ("on", "min_rating", "years")})
        out[k] = e
    return out
