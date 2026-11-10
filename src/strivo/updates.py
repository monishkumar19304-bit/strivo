"""Daily check for fixes: new compatibility rules and new Strivo releases, from the project's GitHub releases.

- **Rules** (`compat.json` + `compat.json.sha256` attached to a release) are data. Depending on the
  `rules_updates` setting they're applied automatically (default), offered ("ask"), or ignored ("off"). They're
  validated before use and the previous copy is kept for rollback.
- **Code** updates are never installed by Strivo. A newer release is announced; you update the container or
  package yourself.
- What's sent: one anonymous HTTPS request to api.github.com (and the asset downloads). No history, no device
  names, no identifiers. The User-Agent is just "Strivo/<version>".
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request

from . import __version__
from . import rules as rules_mod

log = logging.getLogger(__name__)
DAY = 24 * 3600
DEFAULT_REPO = "nickdnj/strivo"


def http_get(url: str, headers: dict, limit: int = rules_mod.MAX_BYTES + 1024,
             timeout: float = 20) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, headers=headers)
    try:
        from .net import ssl_context
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
            return r.status, dict(r.headers), r.read(limit)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), b""


class UpdateChecker:
    def __init__(self, data, rules: rules_mod.Rules, health=None, notify=None, fetch=None):
        self.data, self.rules, self.health, self.notify = data, rules, health, notify
        self.fetch = fetch or http_get

    def state(self) -> dict:
        return self.data.read("updates.json", {}) or {}

    def _save(self, s):
        self.data.write("updates.json", s)

    def due(self, now=None) -> bool:
        s, now = self.state(), now or time.time()
        return now >= max(s.get("last_check", 0) + DAY, s.get("backoff_until", 0))

    def check(self, force: bool = False, now: float | None = None) -> dict:
        now = now or time.time()
        s = self.state()
        if not force and not self.due(now):
            return s
        c = self.data.config()
        repo = c.get("update_repo") or DEFAULT_REPO
        mode = c.get("rules_updates", "auto")
        hdr = {"User-Agent": f"Strivo/{__version__}", "Accept": "application/vnd.github+json"}
        if s.get("etag") and not force:
            hdr["If-None-Match"] = s["etag"]
        s["last_check"] = now
        try:
            code, headers, body = self.fetch(f"https://api.github.com/repos/{repo}/releases/latest", hdr)
        except Exception as e:  # noqa: BLE001
            return self._fail(s, f"couldn't reach GitHub ({type(e).__name__})", now)
        if code == 304:
            s.update(status="up to date", error="", backoff=0, backoff_until=0)
            self._save(s)
            return s
        if code == 404:
            s.update(status="no releases published yet", error="", backoff=0, backoff_until=0)
            self._save(s)
            return s
        if code != 200:
            return self._fail(s, f"GitHub answered {code}", now)
        rel = json.loads(body or b"{}")
        s["etag"] = headers.get("ETag") or headers.get("Etag") or ""
        latest = (rel.get("tag_name") or "").lstrip("v")
        s.update(latest_version=latest, release_url=rel.get("html_url", ""), error="", backoff=0, backoff_until=0)
        # rules-only releases are tagged rules-YYYY.MM.DD and never count as a code update
        s["code_update"] = bool(re.fullmatch(r"\d+\.\d+(\.\d+)?([.\-]?\w+)?", latest)) and rules_mod.newer(latest, __version__)
        if s["code_update"] and s.get("announced") != latest and self.notify:
            self.notify("Strivo update available", f"Strivo {latest} is out. Update your container or package "
                                                    "when convenient; Strivo never updates its own code.")
            s["announced"] = latest
        s["status"] = "up to date"
        assets = {a.get("name"): a.get("browser_download_url") for a in rel.get("assets", [])}
        if mode != "off" and assets.get("compat.json"):
            s.update(self._rules_from(assets, hdr, mode, s, now))
        self._save(s)
        return s

    def _rules_from(self, assets, hdr, mode, s, now) -> dict:
        if not assets.get("compat.json.sha256"):
            return dict(rules_status="release has rules but no checksum; ignored")
        try:
            _, _, sha = self.fetch(assets["compat.json.sha256"], hdr)
            _, _, raw = self.fetch(assets["compat.json"], hdr)
            r = rules_mod.validate(raw, sha.decode(errors="ignore"), __version__)
        except rules_mod.RulesError as e:
            return dict(rules_status=f"rules update rejected: {e}")
        except Exception as e:  # noqa: BLE001
            return dict(rules_status=f"couldn't download rules ({type(e).__name__})")
        if not rules_mod.newer(r["version"], self.rules.version):
            return dict(rules_status=f"rules {self.rules.version} are current", rules_available="")
        if mode == "ask":
            self.data.write("rules_pending.json", dict(raw=raw.decode(), sha=sha.decode(errors="ignore")))
            return dict(rules_status=f"rules {r['version']} available", rules_available=r["version"])
        return self.apply(raw, sha.decode(errors="ignore"), now)

    def apply(self, raw: bytes, sha: str, now=None) -> dict:
        r = self.rules.install(raw, sha)
        n = self.health.mark_verifying() if self.health else 0
        if self.notify:
            self.notify("Strivo installed a compatibility fix",
                        f"Rules {r['version']} are in place." + (f" Checking {n} app(s) that had stopped reporting titles."
                                                                  if n else ""))
        try:
            self.data.file("rules_pending.json").unlink()
        except OSError:
            pass
        return dict(rules_status=f"rules {r['version']} installed", rules_available="", rules_installed_at=now or time.time())

    def apply_pending(self) -> dict:
        p = self.data.read("rules_pending.json", None)
        if not p:
            raise rules_mod.RulesError("no rules update waiting")
        s = self.state()
        s.update(self.apply(p["raw"].encode(), p["sha"]))
        self._save(s)
        return s

    def _fail(self, s, msg, now):
        back = min(max(s.get("backoff", 0) * 2, 3600), DAY)
        s.update(status="check failed", error=msg, backoff=back, backoff_until=now + back)
        self._save(s)
        return s
