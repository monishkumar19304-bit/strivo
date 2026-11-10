"""The streaming-service catalog, read from the compatibility rules (src/strivo/rules/compat.json).

App ids per platform, aliases, TMDB provider names and deep-link templates are data, so a rules update can fix
them without a new Strivo release. If an id is wrong for your box, `strivo probe` shows the id it reported;
a one-line fix to compat.json is the whole PR.
"""
from __future__ import annotations

from . import rules as _rules


def services() -> dict:
    return _rules.current().services()


def streaming() -> list[str]:
    return _rules.current().streaming()


def prov_map() -> dict:
    return _rules.current().providers()


def service_for(app: str | None, app_id: str | None = None) -> str | None:
    """Map whatever a box reports (an app name, bundle id, package or channel id) to a service name."""
    return _rules.current().service_for(app, app_id)


def display_app(app: str | None, app_id: str | None = None) -> str | None:
    """The friendly app name used in history: the service name when known, else what the box said."""
    return service_for(app, app_id) or app or app_id


def launch_id(service: str, platform: str) -> str | None:
    return _rules.current().launch_id(service, platform)


def trailer(platform: str, video_id: str):
    return _rules.current().trailer(platform, video_id)


def __getattr__(name):   # backwards-compatible constants, always reflecting the active rules
    if name == "SERVICES":
        return services()
    if name == "STREAMING":
        return streaming()
    if name == "PROV_MAP":
        return prov_map()
    raise AttributeError(name)
