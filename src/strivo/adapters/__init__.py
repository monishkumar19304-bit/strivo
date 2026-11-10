"""Device adapters. Adding a platform = adding one module here and one line in ADAPTERS."""
from __future__ import annotations

from .base import Adapter, AdapterError, Device, NowPlaying, Status  # noqa: F401

ADAPTERS = {
    "appletv": ("strivo.adapters.appletv", "AppleTVAdapter"),
    "android": ("strivo.adapters.android", "AndroidAdapter"),
    "plex": ("strivo.adapters.plex", "PlexAdapter"),
    "jellyfin": ("strivo.adapters.jellyfin", "JellyfinAdapter"),
    "roku": ("strivo.adapters.roku", "RokuAdapter"),
}


def load(kind: str, data=None) -> Adapter:
    import importlib
    if kind not in ADAPTERS:
        raise KeyError(kind)
    mod, cls = ADAPTERS[kind]
    return getattr(importlib.import_module(mod), cls)(data)


def all_adapters(data=None) -> dict[str, Adapter]:
    out = {}
    for k in ADAPTERS:
        try:
            out[k] = load(k, data)
        except Exception:  # a missing optional library disables just that platform
            pass
    return out
