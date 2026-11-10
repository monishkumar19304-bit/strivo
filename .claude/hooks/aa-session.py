#!/usr/bin/env python3
"""Session bookkeeping for AgentArchitect-managed repos.

  aa-session.py end     (SessionEnd)   record how the session left the repo
  aa-session.py start   (SessionStart) tell the next session what was left behind

Why: `/wrap` and the wiki session log are instructions, and instructions get
skipped. A session that never wraps can leave its work uncommitted for weeks
before anyone notices. This makes the leftover visible at the start of the
next session instead.

`start` prints at most a few lines, and only when there is something to say:
uncommitted files untouched for over a day, or a substantive previous session
that wrote no session log. Whatever it prints is added to Claude's context.

Headless runs (`claude -p`, the nightly jobs) are ignored in both directions:
they neither receive the note nor count as "the previous session".

State lives outside the repo, in ~/.claude/aa-state/, so nothing here ever
shows up in `git status`.

Factory-managed: edit templates/portable/hooks/ in AgentArchitect, then `aa sync`.
Never blocks and never fails a session: every error path exits 0 silently.
"""
import glob
import hashlib
import json
import os
import subprocess
import sys
import time

DAY = 86400
SUBSTANTIVE_TOOL_CALLS = 15   # below this a session is a quick question, not work
STALE_AFTER = DAY             # uncommitted and untouched this long = left behind
FORGET_AFTER = 7 * DAY        # stop mentioning an unlogged session after a week
# Files `aa sync` regenerates; they sit modified between syncs and are not "work left behind".
GENERATED = (".agentarchitect.json", ".claude/agents/", ".claude/skills/", ".claude/hooks/",
             ".claude/settings.json", "CLAUDE.md", ".obsidian/")
# Written by the weekly stale-session digest: {project dir: [session ids not yet wrapped or triaged]}
STALE_SESSIONS = os.path.expanduser("~/.claude/aa-state/stale-sessions.json")


def repo_root(data):
    return os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()


def state_path(root):
    key = hashlib.sha1(os.path.realpath(root).encode()).hexdigest()[:16]
    d = os.path.expanduser("~/.claude/aa-state")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{key}.json")


def is_headless():
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("sdk"):
        return True
    # Fallback: look for `claude -p` / `--print` among our ancestors.
    try:
        pid = os.getppid()
        for _ in range(5):
            out = subprocess.run(["ps", "-o", "ppid=,args=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=2).stdout.strip()
            if not out:
                break
            ppid, _, args = out.partition(" ")
            words = args.split()
            if words and os.path.basename(words[0]) == "claude" \
                    and any(w in ("-p", "--print") for w in words[1:]):
                return True
            pid = int(ppid)
            if pid <= 1:
                break
    except Exception:
        pass
    return False


def dirty_paths(root, timeout):
    """Uncommitted paths in the repo, or None if git is slow or absent."""
    try:
        r = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                           capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    paths = []
    for line in r.stdout.splitlines():
        p = line[3:].split(" -> ")[-1].strip().strip('"')
        if p:
            paths.append(p)
    return paths


def wiki_root():
    return os.environ.get("WIKI_REPO") or os.path.expanduser("~/Workspaces/wiki")


def session_log_written_since(ts):
    """True if any wiki session log was modified at or after ts."""
    wiki = wiki_root()
    if not os.path.isdir(wiki):
        return None  # no wiki here (cloud without bootstrap): cannot tell
    patterns = ["_sessions/*/*.md", "_sessions/*.md", "teams/*/_sessions/*.md",
                "teams/*/_sessions/*/*.md", "projects/*/_sessions/*.md"]
    days = {time.strftime("%Y-%m-%d", time.localtime(t)) for t in (ts, time.time())}
    for pat in patterns:
        for f in glob.glob(os.path.join(wiki, pat)):
            if not any(os.path.basename(f).startswith(d) for d in days):
                continue
            try:
                if os.path.getmtime(f) >= ts - 60:
                    return True
            except OSError:
                pass
    return False


def session_started_at(transcript):
    try:
        st = os.stat(transcript)
        return getattr(st, "st_birthtime", st.st_ctime)
    except Exception:
        return time.time() - 12 * 3600


def count_tool_calls(transcript):
    try:
        n = 0
        with open(transcript, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                n += chunk.count(b'"type":"tool_use"')
        return n
    except Exception:
        return 0


def on_end(data):
    if is_headless():
        return
    root = repo_root(data)
    transcript = data.get("transcript_path") or ""
    tool_calls = count_tool_calls(transcript)
    if tool_calls < SUBSTANTIVE_TOOL_CALLS:
        return
    started = session_started_at(transcript)
    dirty = dirty_paths(root, timeout=1)
    state = {
        "ended": time.time(),
        "started": started,
        "session_id": data.get("session_id"),
        "tool_calls": tool_calls,
        "dirty": None if dirty is None else len(dirty),
        "logged": session_log_written_since(started),
    }
    with open(state_path(root), "w") as f:
        json.dump(state, f)


def on_start(data):
    if data.get("source", "startup") != "startup" or is_headless():
        return
    root = repo_root(data)
    now = time.time()
    notes = []

    paths = [p for p in (dirty_paths(root, timeout=3) or []) if not p.startswith(GENERATED)]
    stale, oldest = 0, now
    for p in paths[:500]:
        try:
            m = os.path.getmtime(os.path.join(root, p))
        except OSError:
            continue
        if now - m > STALE_AFTER:
            stale += 1
            oldest = min(oldest, m)
    if stale:
        days = int((now - oldest) // DAY)
        notes.append(f"{stale} uncommitted file(s) here have not been touched for over a day "
                     f"(oldest: {days} day(s)); {len(paths)} uncommitted in total.")

    try:
        with open(state_path(root)) as f:
            prev = json.load(f)
    except Exception:
        prev = None
    if prev and prev.get("logged") is False and now - prev.get("ended", 0) < FORGET_AFTER \
            and session_log_written_since(prev.get("started", now)) is False:
        when = time.strftime("%a %b %-d %H:%M", time.localtime(prev["ended"]))
        notes.append(f"The previous working session here (ended {when}, "
                     f"{prev.get('tool_calls', '?')} tool calls) wrote no wiki session log.")

    try:
        with open(STALE_SESSIONS) as f:
            stale_ids = json.load(f).get(os.path.realpath(root), [])
        if stale_ids:
            notes.append(f"{len(stale_ids)} earlier session(s) in this directory went quiet without wrapping "
                         f"(listed in the Friday stale-session digest). Offer to triage them; do not start on them unasked.")
    except Exception:
        pass

    if notes:
        print("AgentArchitect session check (informational):")
        for n in notes:
            print(f"- {n}")
        print("Tell Nick this in one line before starting, and offer /wrap if it is this team's "
              "work. Files left by another session are not yours to commit without asking.")


if __name__ == "__main__":
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    try:
        mode = sys.argv[1] if len(sys.argv) > 1 else ""
        if mode == "end":
            on_end(payload)
        elif mode == "start":
            on_start(payload)
    except Exception:
        pass
    sys.exit(0)
