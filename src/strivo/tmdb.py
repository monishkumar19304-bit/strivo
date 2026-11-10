"""TMDB client. The only outside service Strivo talks to, and it only ever sends titles and ids.

Accepts either kind of TMDB credential: a v4 "API Read Access Token" (long, starts with "eyJ") or a v3 API key.
Tests pass a fake `transport` so they never touch the network.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Callable

BASE = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/"


class NoKey(Exception):
    pass


def urllib_transport(url: str, headers: dict, timeout: float = 15) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        return json.load(r)


class TMDB:
    def __init__(self, key: Callable[[], str] | str, transport=None, language: Callable[[], str] | str = "en-US"):
        self._key = key if callable(key) else (lambda: key)
        self._lang = language if callable(language) else (lambda: language)
        self.transport = transport or urllib_transport

    @property
    def configured(self) -> bool:
        return bool(self._key())

    def get(self, path: str, **params) -> dict:
        key = (self._key() or "").strip()
        if not key:
            raise NoKey("no TMDB key yet")
        headers = {"Accept": "application/json"}
        if key.startswith("eyJ"):
            headers["Authorization"] = "Bearer " + key
        else:
            params["api_key"] = key
        params.setdefault("language", self._lang())
        url = BASE + path + "?" + urllib.parse.urlencode(params)
        return self.transport(url, headers)

    def check(self) -> bool:
        """True if the key works (used by the setup wizard)."""
        try:
            self.get("/configuration")
            return True
        except NoKey:
            return False
        except Exception:
            return False
