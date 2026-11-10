#!/usr/bin/env python3
"""PreToolUse guard for the Parallel Agent Git Rules (see CLAUDE.md).

Several agents and sessions share one working tree. The commands below can
destroy or sweep up work that belongs to another of them, so this hook denies
them before they run:

  git reset --hard            git checkout . / git restore .
  git clean -f                git stash (push/save/clear/drop)
  git add -A / --all / .      git commit --no-verify
  git push --force to main

Override: the rules allow an exception when Nick explicitly confirms one. Prefix
the command with AA_GIT_OVERRIDE=1 and the guard lets it through.

Factory-managed: edit templates/portable/hooks/ in AgentArchitect, then `aa sync`.
Fails open: any parse problem lets the command through, so a bug here can never
lock a session out of Bash.
"""
import json
import re
import shlex
import subprocess
import sys

OVERRIDE = "AA_GIT_OVERRIDE=1"
SEPARATORS = {"&&", "||", ";", "|", "&", "(", ")", ";;", "|&"}
GLOBAL_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n.*?\n\s*\2\b", re.S)


def short_flags(args):
    """Letters of every clustered short option: ['-fd', '-x'] -> {'f','d','x'}."""
    out = set()
    for a in args:
        if a.startswith("-") and not a.startswith("--"):
            out.update(a[1:])
    return out


def positionals(args):
    return [a for a in args if not a.startswith("-")]


def current_branch(cwd):
    try:
        return subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd or None, capture_output=True, text=True, timeout=3,
        ).stdout.strip()
    except Exception:
        return ""


def violation(sub, args, cwd):
    """Return (rule, safe alternative) if this git invocation breaks a rule."""
    flags = short_flags(args)
    pos = positionals(args)

    if sub == "reset" and "--hard" in args:
        return ("git reset --hard destroys uncommitted changes, including other agents' work",
                "undo only your own files: git restore --source=<commit> -- <your-files>")
    if sub == "checkout" and "." in pos:
        return ("git checkout . discards every uncommitted change in the tree",
                "name the files: git checkout -- <your-files>")
    if sub == "restore" and "." in pos:
        staged_only = ("--staged" in args or "S" in flags) and not ("--worktree" in args or "W" in flags)
        if not staged_only:
            return ("git restore . discards every uncommitted change in the tree",
                    "name the files: git restore <your-files>")
    if sub == "clean" and ("f" in flags or "--force" in args) \
            and not ("n" in flags or "--dry-run" in args):
        return ("git clean -f deletes untracked files, which may be another agent's new work",
                "delete your own files by path, or preview with git clean -n")
    if sub == "stash":
        action = pos[0] if pos else "push"
        if action not in {"list", "show", "pop", "apply", "branch"}:
            return ("git stash sweeps up ALL uncommitted changes, including other agents' work",
                    "commit your own files to a wip/ branch instead")
    if sub == "add" and ("-A" in args or "--all" in args or "A" in flags or "." in pos):
        return ("git add -A / git add . stages other agents' uncommitted work",
                "stage by path: git add <file> <file> ...")
    if sub == "commit" and ("--no-verify" in args or "n" in flags):
        return ("git commit --no-verify bypasses required hooks",
                "fix what the hook reports, then commit normally")
    if sub == "push":
        forced = ("--force" in args or "f" in flags
                  or any(a.startswith("--force-with-lease") or a.startswith("--force-if-includes") for a in args)
                  or any(p.startswith("+") for p in pos))
        if forced:
            names_main = any(re.search(r"(^|[:+/])(main|master)$", p) for p in pos)
            implicit = (len(pos) <= 1 or "HEAD" in pos) and current_branch(cwd) in {"main", "master"}
            if names_main or implicit:
                return ("force-pushing main overwrites upstream history",
                        "git pull --rebase, resolve conflicts in your own files, then push normally")
    return None


def git_invocations(tokens):
    """Yield (subcommand, args, -C path) for each git call in a token stream."""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok != "git" and not tok.endswith("/git"):
            i += 1
            continue
        i += 1
        cdir = None
        while i < len(tokens) and tokens[i].startswith("-"):
            opt = tokens[i]
            if opt in GLOBAL_OPTS_WITH_VALUE and i + 1 < len(tokens):
                if opt == "-C":
                    cdir = tokens[i + 1]
                i += 2
            else:
                i += 1
        if i >= len(tokens) or tokens[i] in SEPARATORS:
            continue
        sub = tokens[i]
        i += 1
        args = []
        while i < len(tokens) and tokens[i] not in SEPARATORS:
            args.append(tokens[i])
            i += 1
        yield sub, args, cdir


def main():
    data = json.load(sys.stdin)
    if data.get("tool_name") != "Bash":
        return
    command = (data.get("tool_input") or {}).get("command") or ""
    if "git" not in command:
        return

    # Heredoc bodies are data (a commit message, a notes file), not commands.
    stripped = HEREDOC_RE.sub("", command)
    # One command per line: join continuations, then make each newline a separator.
    stripped = stripped.replace("\\\n", " ").replace("\n", " ; ")
    lexer = shlex.shlex(stripped, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    tokens = list(lexer)
    if OVERRIDE in tokens:
        return

    for sub, args, cdir in git_invocations(tokens):
        hit = violation(sub, args, cdir or data.get("cwd"))
        if not hit:
            continue
        rule, alternative = hit
        reason = (
            f"Blocked by the Parallel Agent Git Rules: {rule}. Instead: {alternative}. "
            f"If Nick has explicitly confirmed this exact operation in this conversation, "
            f"re-run it prefixed with {OVERRIDE}. Do not use the override on your own judgment; "
            f"if you are a subagent, report back instead."
        )
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}))
        return


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
