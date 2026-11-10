"""Diagnosis tiers. See docs/SELF-HEALING.md.

- Tier 1 (default, implemented): deterministic rules. No AI, no key, no extra hardware.
- Tier 2 (optional, not implemented yet): a local model, e.g. on a box with enough memory, to read redacted logs
  and suggest a likely cause or a draft rules change.
- Tier 3 (optional, not implemented yet): a cloud model with the user's own key, for drafting a diagnosis or a
  report. Off by default; shows exactly what will be sent and asks first.

Whatever the tier, a model may only *propose*: it never runs code, never installs anything and never posts
anything. Input is always passed through `redact()` first.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .report import sanitize


@dataclass
class Diagnosis:
    summary: str
    likely_cause: str = ""
    next_steps: list[str] = field(default_factory=list)
    tier: int = 1


_REDACT = [
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<ip>"),
    (re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b"), "<mac>"),
    (re.compile(r"(?i)(token|key|password|secret|authorization)[\"'=: ]+\S+"), r"\1=<redacted>"),
    (re.compile(r"\b[0-9a-f]{24,}\b", re.I), "<id>"),
]


def redact(text: str) -> str:
    """Strip addresses, MACs, long ids and credentials from free text before it goes anywhere."""
    for rx, rep in _REDACT:
        text = rx.sub(rep, text or "")
    return text


class Diagnoser:
    tier = 0

    def diagnose(self, entry: dict, failure: str) -> Diagnosis:
        raise NotImplementedError


class RulesDiagnoser(Diagnoser):
    tier = 1

    def diagnose(self, entry, failure):
        app = entry.get("app") or "this box"
        if failure == "unpaired":
            return Diagnosis("The box refused Strivo's credentials.", "It was reset, or its pairing was removed.",
                             ["Remove it under Devices and add it again."])
        if failure == "offline":
            steps = ["Check it's on and on the same network."]
            if entry.get("kind") == "android":
                steps.append("System updates sometimes turn debugging off: check Developer options.")
            if entry.get("kind") == "roku":
                steps.append("Check Control by mobile apps is still set to Permissive.")
            return Diagnosis("Strivo can't reach this box.", "It's off, asleep in a deep power mode, on another "
                             "network, or its address changed.", steps)
        if failure == "verifying":
            return Diagnosis(f"A fix for {app} was installed; waiting to see a title come through.",
                             "Recovery is only confirmed by real playback.", ["Play something in the app for a minute."])
        return Diagnosis(f"{app} is playing but no longer says what.",
                         "The app probably changed what it reports (an app update).",
                         ["Strivo checks daily for a rules fix and installs it automatically.",
                          "If it lasts more than a few days, send a report (it contains no titles or personal info)."])


class LocalModelDiagnoser(Diagnoser):
    """Tier 2 stub. A contribution would implement `diagnose()` against a local model endpoint."""
    tier = 2

    def diagnose(self, entry, failure):
        raise NotImplementedError("local-model diagnosis isn't built yet (see docs/SELF-HEALING.md)")


class CloudModelDiagnoser(Diagnoser):
    """Tier 3 stub: bring-your-own-key. Must send only `sanitize()`d fields plus `redact()`ed text, after approval."""
    tier = 3

    def prepare(self, entry, failure, log_excerpt: str = "") -> dict:
        """What *would* be sent, for the user to review. Nothing is sent by this method."""
        return dict(report=sanitize(entry, failure), log=redact(log_excerpt)[:4000])

    def diagnose(self, entry, failure):
        raise NotImplementedError("cloud-model diagnosis isn't built yet (see docs/SELF-HEALING.md)")


def get(config: dict) -> Diagnoser:
    """The configured tier, falling back to tier 1 for anything not implemented."""
    return RulesDiagnoser()
