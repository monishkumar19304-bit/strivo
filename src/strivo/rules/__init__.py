"""Compatibility rules: the fragile parts of every integration, kept as data instead of code.

Streaming apps change what they report; when they do, the fix is usually a new regex, a new app id or a new
expectation, not new code. Those live in `compat.json`:

- `services`      per-platform app ids (tvOS bundle, Android package, Fire TV package, Roku channel) + aliases
- `streaming`     services offered in "My services"; `providers` TMDB provider names -> service names
- `title_rules`   per-app regexes that pull show / season / episode out of what the box sends
- `launch`        deep-link templates (trailers)
- `expect`        what each app is known to report on each platform (used to detect breakage)

A copy is bundled with Strivo. A newer copy can be installed from a GitHub release (`strivo.updates`): it is
validated (schema, size, regex compile, checksum, minimum Strivo version) before use, saved next to the previous
one, and can be rolled back. Rules are data only: nothing in this file is ever executed.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from importlib import resources
from pathlib import Path

SCHEMA = 1
MAX_BYTES = 512 * 1024
PLATFORMS = ("appletv", "android", "firetv", "roku")
LEVELS = ("app-only", "title", "video", "channel", "episode")
_TOP_KEYS = {"schema", "version", "min_strivo", "about", "services", "streaming", "providers", "title_rules",
             "launch", "expect"}


class RulesError(ValueError):
    pass


def _vkey(v: str) -> tuple:
    """Order versions like 2026.11.10.1 or 0.1.0.dev0 without extra dependencies."""
    out = []
    for part in re.split(r"[.\-+]", str(v)):
        m = re.match(r"(\d*)(.*)", part)
        out.append((int(m.group(1)) if m.group(1) else -1, m.group(2)))
    return tuple(out)


def newer(a: str, b: str) -> bool:
    return _vkey(a) > _vkey(b)


def validate(raw: bytes, expected_sha256: str | None = None, strivo_version: str | None = None) -> dict:
    """Parse and check a rules file. Raises RulesError with a reason a person can read."""
    if len(raw) > MAX_BYTES:
        raise RulesError("rules file is too large")
    if expected_sha256 is not None:
        got = hashlib.sha256(raw).hexdigest()
        if got.lower() != expected_sha256.strip().split()[0].lower():
            raise RulesError("checksum doesn't match")
    try:
        r = json.loads(raw)
    except ValueError as e:
        raise RulesError(f"not valid JSON ({e})") from e
    if not isinstance(r, dict) or r.get("schema") != SCHEMA:
        raise RulesError("unsupported rules schema")
    if set(r) - _TOP_KEYS:
        raise RulesError("unknown sections: " + ", ".join(sorted(set(r) - _TOP_KEYS)))
    if not isinstance(r.get("version"), str):
        raise RulesError("missing version")
    if strivo_version and r.get("min_strivo") and newer(r["min_strivo"], strivo_version):
        raise RulesError(f"needs Strivo {r['min_strivo']} or newer")
    for name, s in (r.get("services") or {}).items():
        ids = s.get("ids") if isinstance(s, dict) else None
        if not isinstance(ids, dict) or any(k not in PLATFORMS or not re.fullmatch(r"[\w.\-]{1,120}", str(v))
                                            for k, v in ids.items()):
            raise RulesError(f"bad app ids for {name}")
    for app, rules in (r.get("title_rules") or {}).items():
        for rule in rules:
            if rule.get("kind") not in ("episode", "title", "live", "video"):
                raise RulesError(f"bad rule kind for {app}")
            if rule.get("match") is not None:
                if len(rule["match"]) > 300:
                    raise RulesError(f"rule for {app} is too long")
                try:
                    re.compile(rule["match"])
                except re.error as e:
                    raise RulesError(f"bad regex for {app}: {e}") from e
            if rule.get("field", "title") not in ("title", "artist", "album"):
                raise RulesError(f"bad field for {app}")
    for plat, t in ((r.get("launch") or {}).get("trailer") or {}).items():
        if plat not in PLATFORMS or "{id}" not in str(t.get("link", "")):
            raise RulesError(f"bad trailer template for {plat}")
    for plat, apps in (r.get("expect") or {}).items():
        if plat.startswith("_"):
            continue
        if not isinstance(apps, dict):
            raise RulesError(f"bad expectations for {plat}")
        for app, lvl in apps.items():
            if not app.startswith("_") and lvl not in LEVELS:
                raise RulesError(f"bad expectation {app}={lvl}")
    return r


class Rules:
    """The active rules: bundled, or an installed update from the data folder."""

    def __init__(self, data_dir: str | Path | None = None, strivo_version: str | None = None):
        self.lock = threading.RLock()
        self.data_dir = Path(data_dir) if data_dir else None
        self.strivo_version = strivo_version
        self.bundled = validate((resources.files("strivo.rules") / "compat.json").read_bytes())
        self.source = "bundled"
        self.r = self.bundled
        self._load_installed()
        self._index()

    # ---- files in the data folder
    def _path(self, name="compat.json") -> Path | None:
        return self.data_dir / "rules" / name if self.data_dir else None

    def _load_installed(self):
        p = self._path()
        if not p or not p.exists():
            return
        try:
            r = validate(p.read_bytes(), strivo_version=self.strivo_version)
        except (RulesError, OSError):
            return   # a damaged update never breaks Strivo: fall back to the bundled copy
        if newer(r["version"], self.bundled["version"]):
            self.r, self.source = r, "update"

    def install(self, raw: bytes, sha256: str | None) -> dict:
        """Validate and activate a downloaded rules file; keep the current one for rollback."""
        r = validate(raw, sha256, self.strivo_version)
        with self.lock:
            if not newer(r["version"], self.version):
                raise RulesError(f"rules {r['version']} aren't newer than {self.version}")
            p = self._path()
            if p is None:
                raise RulesError("no data folder")
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists():
                p.replace(self._path("compat.previous.json"))
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(raw)
            tmp.replace(p)
            self.r, self.source = r, "update"
            self._index()
        return r

    def rollback(self) -> str:
        """Go back to the previous installed copy, or to the bundled rules."""
        with self.lock:
            p, prev = self._path(), self._path("compat.previous.json")
            if p and p.exists():
                p.unlink()
            if prev and prev.exists():
                prev.replace(p)
            self.r, self.source = self.bundled, "bundled"
            self._load_installed()
            self._index()
            return self.version

    # ---- lookups
    @property
    def version(self) -> str:
        return self.r["version"]

    def _index(self):
        rev = {}
        for name, s in self.r.get("services", {}).items():
            rev[name.lower()] = name
            for a in s.get("aliases", []):
                rev[a.lower()] = name
            for v in s.get("ids", {}).values():
                rev[str(v).lower()] = name
        self._rev = rev
        self._compiled = {app: [dict(rule, rx=re.compile(rule["match"]) if rule.get("match") else None)
                                for rule in rules] for app, rules in self.r.get("title_rules", {}).items()}

    def services(self) -> dict:
        return {k: dict(v.get("ids", {}), aliases=v.get("aliases", [])) for k, v in self.r.get("services", {}).items()}

    def streaming(self) -> list[str]:
        return list(self.r.get("streaming", []))

    def providers(self) -> dict:
        return dict(self.r.get("providers", {}))

    def service_for(self, app, app_id=None):
        for v in (app_id, app):
            if v and str(v).lower() in self._rev:
                return self._rev[str(v).lower()]
        return None

    def launch_id(self, service, platform):
        name = service if service in self.r.get("services", {}) else self.service_for(service)
        return ((self.r.get("services", {}).get(name) or {}).get("ids") or {}).get(platform)

    def trailer(self, platform, video_id):
        t = ((self.r.get("launch") or {}).get("trailer") or {}).get(platform)
        if not t:
            return None
        return t["app"], t["link"].replace("{id}", video_id)

    def title_rules(self, app) -> list[dict]:
        return self._compiled.get(app, [])

    def expect(self, platform, app) -> str | None:
        return ((self.r.get("expect") or {}).get(platform) or {}).get(app)

    def summary(self) -> dict:
        return dict(version=self.version, source=self.source, bundled=self.bundled["version"])


_current: Rules | None = None


def current() -> Rules:
    global _current
    if _current is None:
        _current = Rules()
    return _current


def use(rules: Rules) -> Rules:
    """Make `rules` the active set (the server does this with its data folder)."""
    global _current
    _current = rules
    return rules


def copy_of_bundled() -> dict:
    return copy.deepcopy(current().bundled)
