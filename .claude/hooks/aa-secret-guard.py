#!/usr/bin/env python3
"""PreToolUse guard: credentials do not go into memory or the wiki.

Memory files and the wiki are read by every agent and synced to GitHub. A
password, key or token written there is a leak, so this denies Write/Edit calls
into those places when the new text holds a credential value, and says what to
do instead: put the secret in Apple Passwords (or the Keychain through
`aa secret`) and write a pointer such as "Apple Passwords: Finley Wi-Fi".

Guarded paths: ~/.claude/projects/*/memory/ and the wiki repo ($WIKI_REPO,
default ~/Workspaces/wiki).

Factory-managed: edit templates/portable/hooks/ in AgentArchitect, then `aa sync`.
Fails open: any parse problem lets the write through.
"""
import json
import os
import re
import sys

HOME = os.path.expanduser("~")
WIKI = os.path.realpath(os.path.expanduser(os.environ.get("WIKI_REPO") or "~/Workspaces/wiki"))
MEMORY_RE = re.compile(r"/\.claude/projects/[^/]+/memory/")

TOKEN_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{30,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"\bxox[bpas]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
LABEL = r"(?:pass(?:word|phrase|code)?|passwd|pwd|psk|pre-?shared key|secret|api[ _-]?key|(?:access|auth|bearer|refresh|bot|oauth)[ _-]?token|token|client[ _-]?secret)"
KV_RE = re.compile(r"(?i)\b" + LABEL + r"\b[\s*`\"']*[:=][\s*`\"']*([^\s`\"'*,;|)]{6,})")
PLACEHOLDER = re.compile(
    r"(?i)^(<.*>|\$\{?\w+\}?|\{\{.*\}\}|x{3,}|\*{3,}|█+|\.{3,}|redacted|none|null|unset|changeme|your[-_ ]?\w*|"
    r"see|stored|in|apple|passwords?|keychain|env|environment|\$\(.*\)|https?://.*|/.*|~.*)$")


def guarded(path):
    if not path:
        return False
    real = os.path.realpath(path)
    return bool(MEMORY_RE.search(real)) or real == WIKI or real.startswith(WIKI + os.sep)


def texts(data):
    ti = data.get("tool_input") or {}
    for key in ("content", "new_string"):
        if isinstance(ti.get(key), str):
            yield ti[key]
    for e in ti.get("edits") or []:
        if isinstance(e, dict) and isinstance(e.get("new_string"), str):
            yield e["new_string"]


def find_secret(text):
    for pat in TOKEN_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)[:6] + "…"
    for m in KV_RE.finditer(text):
        if not PLACEHOLDER.match(m.group(1)):
            return m.group(0).split(m.group(1))[0].strip() + " …"
    return None


def main():
    data = json.load(sys.stdin)
    if data.get("tool_name") not in ("Write", "Edit", "MultiEdit"):
        return
    path = (data.get("tool_input") or {}).get("file_path")
    if not guarded(path):
        return
    for t in texts(data):
        hit = find_secret(t)
        if hit:
            reason = (f"This looks like a credential ({hit}) going into memory or the wiki, which agents read and GitHub stores. "
                      "Do not write the secret. Save it in Apple Passwords (or `aa secret set <name>` for the Keychain) and write only a pointer, "
                      "e.g. 'Apple Passwords: <entry name>'. Then retry the write without the value.")
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason}}))
            return


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
