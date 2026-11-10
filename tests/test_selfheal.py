"""Self-healing compatibility: rules as data, health states, daily fix check, verified recovery, safe reports."""
import hashlib
import json
import time

import pytest

from fakes import dev
from strivo import __version__
from strivo import rules as rules_mod
from strivo.adapters.base import NowPlaying, PLAYING
from strivo.diagnose import CloudModelDiagnoser, RulesDiagnoser, redact
from strivo.health import HealthTracker
from strivo.history import norm
from strivo.report import find_existing_issue, issue_url, os_family
from strivo.updates import UpdateChecker


@pytest.fixture
def rules(data):
    r = rules_mod.use(rules_mod.Rules(data.path, __version__))
    yield r
    rules_mod.use(rules_mod.Rules())


def newer_rules(**changes):
    r = rules_mod.copy_of_bundled()
    r["version"] = "2099.1.1.1"
    r.update(changes)
    return json.dumps(r).encode()


def sha(b):
    return hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------- rules
def test_bundled_rules_drive_parsing_and_catalog(rules):
    assert rules.source == "bundled" and rules.service_for(None, "com.hulu.livingroomplus") == "Hulu"
    assert rules.launch_id("Hulu", "roku") == "2285"
    assert rules.trailer("android", "abcdefghijk")[1] == "https://www.youtube.com/watch?v=abcdefghijk"
    assert rules.expect("appletv", "Netflix") == "app-only"
    n = norm(dict(app="Hulu", title="Abbott Elementary | S3 E4 - Career Day"))
    assert (n["series"], n["season"], n["ep"]) == ("Abbott Elementary", 3, 4)


def test_install_validate_and_rollback(rules, data):
    raw = newer_rules()
    with pytest.raises(rules_mod.RulesError, match="checksum"):
        rules.install(raw, "0" * 64)
    bad = newer_rules(title_rules={"Hulu": [{"field": "title", "match": "(", "kind": "episode", "set": {}}]})
    with pytest.raises(rules_mod.RulesError, match="bad regex"):
        rules.install(bad, sha(bad))
    future = newer_rules(min_strivo="99.0")
    with pytest.raises(rules_mod.RulesError, match="needs Strivo"):
        rules.install(future, sha(future))
    code = newer_rules()
    code_like = json.loads(code)
    code_like["exec"] = "import os"
    code_like = json.dumps(code_like).encode()
    with pytest.raises(rules_mod.RulesError, match="unknown sections"):
        rules.install(code_like, sha(code_like))
    rules.install(raw, sha(raw))
    assert rules.version == "2099.1.1.1" and rules.source == "update"
    assert rules_mod.Rules(data.path, __version__).version == "2099.1.1.1"     # survives a restart
    with pytest.raises(rules_mod.RulesError, match="aren't newer"):
        rules.install(raw, sha(raw))
    assert rules.rollback() == rules.bundled["version"] and rules.source == "bundled"


def test_damaged_installed_rules_fall_back_to_bundled(data):
    (data.path / "rules").mkdir()
    (data.path / "rules" / "compat.json").write_text("{not json")
    r = rules_mod.Rules(data.path, __version__)
    assert r.source == "bundled"


# ---------------------------------------------------------------- health
def watch(h, d, np, minutes_apart, n, start):
    for i in range(n):
        h.observe(d, np, now=start + i * minutes_apart * 60)


def test_degraded_only_after_repeated_sessions_and_not_for_known_limits(data, rules):
    h = HealthTracker(data)
    tv = dev("appletv", "Living Room", os_version="18.1")
    t0 = time.time()
    h.observe(tv, NowPlaying(state=PLAYING, app="Netflix"), now=t0)          # Netflix never reports titles here
    watch(h, tv, NowPlaying(state=PLAYING, app="Hulu"), 1, 5, t0)            # same session: counts once
    states = {a["app"]: a["state"] for a in h.apps()}
    assert states == {"Netflix": "app-only", "Hulu": "unknown"}
    watch(h, tv, NowPlaying(state=PLAYING, app="Hulu"), 30, 3, t0 + 3600)    # 3 separate sessions
    hulu = next(a for a in h.apps() if a["app"] == "Hulu")
    assert hulu["state"] == "degraded" and hulu["expected"] == "episode"
    assert [e["to"] for e in h.events][-1] == "degraded"


def test_an_app_that_used_to_report_titles_is_watched_too(data, rules):
    h = HealthTracker(data)
    box = dev("android", "Den", platform="android")
    t0 = time.time()
    h.observe(box, NowPlaying(state=PLAYING, app="Some App", title="A Show S1E1"), now=t0)   # no expectation in rules
    watch(h, box, NowPlaying(state=PLAYING, app="Some App"), 30, 3, t0 + 60)
    assert h.apps()[0]["state"] == "degraded"


def test_box_states(data, rules):
    h = HealthTracker(data)
    devs = [dev("appletv", "A"), dev("plex", "P"), dev("roku", "R"), dev("android", "Off", enabled=False)]
    live = {"a": dict(online=True), "p": dict(online=False, error="P refused the token (401)"),
            "r": dict(online=False, error="timed out")}
    assert [b["state"] for b in h.boxes(devs, live)] == ["healthy", "unpaired", "offline", "paused"]


# ---------------------------------------------------------------- the whole loop
class FakeGitHub:
    def __init__(self, rules_raw=None, with_sha=True, tag="v0.1.0.dev0", code=200):
        self.rules_raw, self.with_sha, self.tag, self.code, self.calls = rules_raw, with_sha, tag, code, []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        if url.endswith("/releases/latest"):
            if self.code != 200:
                return self.code, {}, b""
            assets = []
            if self.rules_raw:
                assets.append({"name": "compat.json", "browser_download_url": "https://x.test/compat.json"})
                if self.with_sha:
                    assets.append({"name": "compat.json.sha256", "browser_download_url": "https://x.test/compat.json.sha256"})
            return 200, {"ETag": "e1"}, json.dumps({"tag_name": self.tag, "html_url": "https://x.test/r",
                                                    "assets": assets}).encode()
        if url.endswith(".sha256"):
            return 200, {}, (sha(self.rules_raw) + "  compat.json\n").encode()
        return 200, {}, self.rules_raw


def test_detect_fix_verify_recover(data, rules):
    """Max stops filling the title (an app update); a rules fix arrives; tracking is proven to recover."""
    notes = []
    h = HealthTracker(data)
    tv = dev("appletv", "Living Room")
    t0 = time.time() - 5 * 3600
    h.observe(tv, NowPlaying(state=PLAYING, app="HBO Max", title="Pilot", artist="A Drama"), now=t0)
    broken = NowPlaying(state=PLAYING, app="HBO Max", album="A Drama")          # title moved to album
    watch(h, tv, broken, 30, 3, t0 + 600)
    assert next(a for a in h.apps())["state"] == "degraded"
    fix_rules = rules_mod.copy_of_bundled()["title_rules"]
    fix_rules["HBO Max"].append({"field": "album", "match": "(.+)", "kind": "title", "set": {"series": 1}})
    raw = newer_rules(title_rules=fix_rules)
    gh = FakeGitHub(raw)
    up = UpdateChecker(data, rules, h, notify=lambda t, m: notes.append(t), fetch=gh)
    s = up.check()
    assert "installed" in s["rules_status"] and rules.version == "2099.1.1.1"
    assert h.apps()[0]["state"] == "verifying"                                  # an update alone proves nothing
    assert all(c[1]["User-Agent"].startswith("Strivo/") and "Authorization" not in c[1] for c in gh.calls)
    h.observe(tv, broken, now=time.time())                                      # real playback, now parsed
    assert h.apps()[0]["state"] == "recovered"
    assert any("fix" in n for n in notes)
    assert up.check() == up.state()                                             # cached: not due again today


def test_update_modes_and_failures(data, rules):
    raw = newer_rules()
    data.update_config(lambda c: c.__setitem__("rules_updates", "ask"))
    up = UpdateChecker(data, rules, None, fetch=FakeGitHub(raw))
    assert up.check()["rules_available"] == "2099.1.1.1" and rules.version != "2099.1.1.1"
    up.apply_pending()
    assert rules.version == "2099.1.1.1"
    rules.rollback()
    data.update_config(lambda c: c.__setitem__("rules_updates", "auto"))
    s = UpdateChecker(data, rules, None, fetch=FakeGitHub(raw, with_sha=False)).check(force=True)
    assert "no checksum" in s["rules_status"] and rules.source == "bundled"
    s = UpdateChecker(data, rules, None, fetch=FakeGitHub(code=404)).check(force=True)
    assert s["status"] == "no releases published yet"
    s = UpdateChecker(data, rules, None, fetch=FakeGitHub(code=500)).check(force=True)
    assert s["status"] == "check failed" and s["backoff_until"] > time.time()


def test_code_updates_are_only_announced_once(data, rules):
    notes = []
    up = UpdateChecker(data, rules, None, notify=lambda t, m: notes.append(m), fetch=FakeGitHub(tag="v9.0.0"))
    assert up.check(force=True)["code_update"] is True
    up.check(force=True)
    assert len(notes) == 1 and "never updates its own code" in notes[0]


# ---------------------------------------------------------------- reports and AI tiers
def test_report_contains_nothing_personal(rules):
    entry = dict(device="nicks-living-room", device_name="Nick's Living Room", kind="android", platform="firetv",
                 app="Hulu", state="degraded", expected="episode", last_level="app-only", last_good=time.time() - 3 * 86400,
                 os="Fire OS (Android 9)", key="nicks-living-room|Hulu", host="192.168.1.50", identifier="G070VM1234",
                 last_title="Secret Show", token="tok-PRIVATE-9")
    url, fields = issue_url(entry, "degraded")
    assert fields["app"] == "Hulu" and fields["platform"] == "Fire TV" and fields["os"] == "Fire OS"
    assert fields["days_since_last_good"] == 3
    import urllib.parse
    sent = json.dumps(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)) + json.dumps(fields)
    for bad in ("Nick", "living", "192.168", "G070VM", "Secret", "tok-PRIVATE"):
        assert bad.lower() not in sent.lower(), bad
    _, f2 = issue_url(dict(entry, app="com.private.sideloaded"), "degraded")
    assert f2["app"] == "another app"
    assert os_family("27.3") == "27" and os_family("Android 14") == "Android 14"


def test_report_reuses_matching_open_compatibility_issue(rules):
    calls = []

    def fetch(url, headers):
        calls.append((url, headers))
        return 200, {}, json.dumps({"items": [{
            "state": "open", "labels": [{"name": "compatibility"}],
            "title": "[compat] Hulu on Apple TV: titles stopped coming through",
            "body": "| failure | degraded |", "html_url": "https://github.com/test/repo/issues/42"
        }]}).encode()

    entry = dict(kind="appletv", platform="appletv", app="Hulu")
    assert find_existing_issue(entry, "degraded", repo="test/repo", fetch=fetch).endswith("/issues/42")
    assert find_existing_issue(entry, "degraded", repo="test/repo", fetch=fetch).endswith("/issues/42")
    assert len(calls) == 1
    assert "is%3Aissue" in calls[0][0] and "Authorization" not in calls[0][1]


def test_report_does_not_reuse_nonmatching_or_unlabelled_issue(rules):
    def fetch(url, headers):
        return 200, {}, json.dumps({"items": [
            {"state": "closed", "labels": [{"name": "compatibility"}],
             "title": "Hulu on Apple TV: titles stopped coming through",
             "body": "degraded", "html_url": "https://example.test/closed"},
            {"state": "open", "labels": [{"name": "bug"}],
             "title": "Hulu on Apple TV: titles stopped coming through",
             "body": "degraded", "html_url": "https://example.test/unlabelled"},
        ]}).encode()

    assert find_existing_issue(dict(kind="appletv", platform="appletv", app="Hulu"),
                               "degraded", repo="test/nonmatching", fetch=fetch) is None


@pytest.mark.parametrize("item", [
    {"state": "open", "pull_request": {"url": "https://api.github.com/pulls/42"},
     "labels": [{"name": "compatibility"}], "title": "Hulu on Apple TV: titles stopped coming through",
     "body": "degraded", "html_url": "https://github.com/test/repo/issues/42"},
    {"state": "open", "labels": [{"name": "compatibility"}],
     "title": "Hulu on Apple TV: titles stopped coming through", "body": "degraded",
     "html_url": "https://evil.example/issues/42"},
])
def test_report_rejects_pull_requests_and_untrusted_urls(item, rules):
    def fetch(url, headers):
        return 200, {}, json.dumps({"items": [item]}).encode()

    assert find_existing_issue(dict(kind="appletv", platform="appletv", app="Hulu"),
                               "degraded", repo="test/restricted", fetch=fetch) is None


def test_report_does_not_match_private_app_as_another_app(rules):
    calls = []

    def fetch(url, headers):
        calls.append(url)
        return 200, {}, b'{"items":[]}'

    assert find_existing_issue(dict(kind="appletv", platform="appletv", app="com.private.sideloaded"),
                               "degraded", repo="test/private", fetch=fetch) is None
    assert calls == []


def test_report_search_failure_falls_back_to_new_issue(rules):
    def fetch(url, headers):
        raise OSError("offline")

    assert find_existing_issue(dict(kind="appletv", platform="appletv", app="Hulu"),
                               "degraded", repo="test/failure", fetch=fetch) is None


def test_ai_tiers_are_stubs_with_redaction():
    assert RulesDiagnoser().diagnose({"app": "Hulu"}, "degraded").summary
    assert redact("host 192.168.1.5 mac aa:bb:cc:dd:ee:ff token=abc123") == "host <ip> mac <mac> token=<redacted>"
    with pytest.raises(NotImplementedError):
        CloudModelDiagnoser().diagnose({}, "degraded")
    prep = CloudModelDiagnoser().prepare(dict(app="Hulu", kind="appletv", platform="appletv"), "degraded",
                                         "error at 10.0.0.2 key: sk-1234")
    assert "10.0.0.2" not in prep["log"] and "sk-1234" not in prep["log"]


def test_rules_only_release_is_not_a_code_update(data, rules):
    s = UpdateChecker(data, rules, None, fetch=FakeGitHub(tag="rules-2099.01.01")).check(force=True)
    assert s["code_update"] is False


def test_connection_refused_is_offline_not_unpaired(data, rules):
    h = HealthTracker(data)
    live = {"a": dict(online=False, error="couldn't connect to A over ADB (ConnectionRefusedError: [Errno 61] Connection refused)")}
    assert h.boxes([dev("android", "A")], live)[0]["state"] == "offline"


def test_version_order_prerelease_before_final():
    """Regression: 0.1.0 must count as newer than 0.1.0.dev0, or a real release rejects every rules fix."""
    order = ["0.1.0.dev0", "0.1.0a1", "0.1.0rc1", "0.1.0", "v0.1.1", "0.2.0.dev0", "0.2.0", "1.0"]
    for lo, hi in zip(order, order[1:]):
        assert rules_mod.newer(hi, lo) and not rules_mod.newer(lo, hi), (lo, hi)
    assert not rules_mod.newer("0.1", "0.1.0") and not rules_mod.newer("0.1.0", "0.1")
    assert rules_mod.newer("2026.11.10.2", "2026.11.10.1") and rules_mod.newer("2099.1.1.1", "2026.11.10.1")
