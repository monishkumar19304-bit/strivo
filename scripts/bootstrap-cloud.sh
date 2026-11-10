#!/usr/bin/env bash
# Cloud / iOS bootstrap for an AgentArchitect-provisioned workspace or project repo.
#
# Run this FIRST in any sandbox that is not Nick's MacBook — claude.ai/code on
# iOS or web, or a fresh remote sandbox. It brings up the wiki knowledge base,
# proves whether this sandbox can push, and reports what the team can and
# cannot do here.
#
# This is a SIBLING of AgentArchitect/scripts/bootstrap-cloud.sh, not a copy.
# The factory's version regenerates .claude/ from agent sources; a spawned repo
# has no factory to regenerate from, so its generated agents are committed and
# this script VERIFIES them instead (step 2).
#
# Factory-managed: copied here by `aa sync` from
# AgentArchitect/templates/portable/scripts/. Do not edit in place.
#
# Env overrides:
#   WIKI_REPO       Where the wiki lives (default: $HOME/Workspaces/wiki)
#   WIKI_REPO_URL   Where to clone from  (default: https://github.com/nickdnj/wiki.git)
#   GIT_USER_EMAIL  Identity for commits (default: nickd@demarconet.com)
#   SKIP_PUSH_CHECK Set to 1 to skip the push dry-run (offline testing)
#
# Exit codes:
#   0  success
#   1  missing prerequisites
#   2  wiki path collision (path exists but is not a git repo)
#   3  wiki clone failed (auth — run `gh auth login`)
#   4  no push credentials for the wiki (read-only mode; session can still read)
#   5  agent surface missing (this clone predates the tracked-.claude fix)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WIKI_REPO_URL="${WIKI_REPO_URL:-https://github.com/nickdnj/wiki.git}"
WIKI_REPO_PATH="${WIKI_REPO:-$HOME/Workspaces/wiki}"
GIT_USER_EMAIL="${GIT_USER_EMAIL:-nickd@demarconet.com}"
GIT_USER_NAME="${GIT_USER_NAME:-Nick DeMarco}"

echo "==> Cloud bootstrap"
echo "    Repo:        $REPO_ROOT"
echo "    Wiki path:   $WIKI_REPO_PATH"
echo "    Wiki source: $WIKI_REPO_URL"
echo

# -----------------------------------------------------------------------------
# 1. Prerequisites — git only. node and gh are nice to have, not required.
# -----------------------------------------------------------------------------
if ! command -v git >/dev/null 2>&1; then
  echo "ERROR: git not found. Nothing here works without it." >&2
  exit 1
fi
for opt in node gh; do
  if command -v "$opt" >/dev/null 2>&1; then
    echo "    [have] $opt"
  else
    echo "    [none] $opt — not required, some conveniences unavailable"
  fi
done
echo

# -----------------------------------------------------------------------------
# 2. Verify the agent surface actually made it into this clone
# -----------------------------------------------------------------------------
agent_count=$(find "$REPO_ROOT/.claude/agents" -maxdepth 1 -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
skill_count=$(find "$REPO_ROOT/.claude/skills" -mindepth 2 -name 'SKILL.md' 2>/dev/null | wc -l | tr -d ' ')

if [[ "$agent_count" -eq 0 ]]; then
  echo "ERROR: no agents in .claude/agents/ — this clone has nothing to run." >&2
  echo "       The generated .claude/ surface is gitignored in this repo, so it" >&2
  echo "       never reached GitHub. On the Mac:" >&2
  echo "         1. remove .claude/agents/*.md and .claude/skills/*/SKILL.md from .gitignore" >&2
  echo "         2. aa sync $REPO_ROOT" >&2
  echo "         3. git add .claude/agents .claude/skills && git commit && git push" >&2
  exit 5
fi
echo "==> Agent surface OK — $agent_count agents, $skill_count skills"

# -----------------------------------------------------------------------------
# 3. Wiki: clone if missing, fast-forward if present
# -----------------------------------------------------------------------------
if [[ -d "$WIKI_REPO_PATH/.git" ]]; then
  echo "==> Wiki present — fast-forwarding"
  git -C "$WIKI_REPO_PATH" pull --ff-only || {
    echo "    [WARN] pull --ff-only failed (local commits or divergence)." >&2
    echo "           Leaving the checkout as-is. Resolve before pushing." >&2
  }
elif [[ -e "$WIKI_REPO_PATH" ]]; then
  echo "ERROR: $WIKI_REPO_PATH exists but is not a git repo." >&2
  echo "       Move it aside or set WIKI_REPO to a different path." >&2
  exit 2
else
  echo "==> Cloning wiki -> $WIKI_REPO_PATH"
  mkdir -p "$(dirname "$WIKI_REPO_PATH")"
  # Full clone, deliberately. The wiki's .git is ~11 MB, so --depth 1 saves
  # nothing, and shallow history breaks `pull --rebase` and wiki-ingest's
  # `git log --since` audit.
  if ! git clone "$WIKI_REPO_URL" "$WIKI_REPO_PATH"; then
    echo >&2
    echo "ERROR: could not clone the wiki." >&2
    echo >&2
    echo "  EXPECTED in a cloud sandbox. Not a bug, and nothing is misconfigured." >&2
    echo >&2
    echo "  The sandbox is issued a GitHub token scoped to the repo you OPENED." >&2
    echo "  nickdnj/wiki is a second private repo, so it is out of scope until you" >&2
    echo "  add it to THIS session." >&2
    echo >&2
    echo "  FIX: attach / add the nickdnj/wiki repo to this session, then re-run" >&2
    echo "       this script. It is idempotent. Takes about ten seconds." >&2
    echo >&2
    echo "  This is per-session by design and there is no permanent setting that" >&2
    echo "  avoids it. Verified 2026-08-17 across three separate sandboxes with the" >&2
    echo "  Claude GitHub app already set to 'All repositories' with read+write to" >&2
    echo "  code — the app installation does not govern sandbox token scope. Do not" >&2
    echo "  go hunting through GitHub settings; just attach the repo." >&2
    echo >&2
    echo "  Until then you are in READ-ONLY-WIKI mode: work normally, but surface" >&2
    echo "  any wiki change as a markdown block for Nick. Do not claim it saved." >&2
    exit 3
  fi
fi

# The agents resolve the wiki as ${WIKI_REPO:-$HOME/Workspaces/wiki}. If the
# clone ended up somewhere else (a sandbox that works out of /workspace, an
# explicit WIKI_REPO), leave a symlink at the default so the fallback also
# lands — otherwise every agent that trusts the default reads nothing.
if [[ "$WIKI_REPO_PATH" != "$HOME/Workspaces/wiki" && ! -e "$HOME/Workspaces/wiki" ]]; then
  mkdir -p "$HOME/Workspaces"
  if ln -s "$WIKI_REPO_PATH" "$HOME/Workspaces/wiki" 2>/dev/null; then
    echo "==> Symlinked \$HOME/Workspaces/wiki -> $WIKI_REPO_PATH (default path resolves)"
  fi
fi

# -----------------------------------------------------------------------------
# 4. Git identity — repo-local, never --global
# -----------------------------------------------------------------------------
for repo in "$WIKI_REPO_PATH" "$REPO_ROOT"; do
  if [[ -z "$(git -C "$repo" config user.email || true)" ]]; then
    git -C "$repo" config user.email "$GIT_USER_EMAIL"
    git -C "$repo" config user.name "$GIT_USER_NAME"
    echo "==> Set git identity in $(basename "$repo"): $GIT_USER_NAME <$GIT_USER_EMAIL>"
  fi
done

# -----------------------------------------------------------------------------
# 5. Can this sandbox actually push the wiki? Answer it now, not at /wrap time.
# -----------------------------------------------------------------------------
push_ok=1
if [[ "${SKIP_PUSH_CHECK:-0}" == "1" ]]; then
  echo "==> SKIP_PUSH_CHECK=1 — not testing wiki push"
else
  echo "==> Testing wiki push credentials (dry run, writes nothing)"
  if git -C "$WIKI_REPO_PATH" push --dry-run origin HEAD >/dev/null 2>&1; then
    echo "    [OK] wiki is writable from this sandbox"
  else
    push_ok=0
    echo "    [READ-ONLY] cannot push to the wiki from here." >&2
  fi
fi

# -----------------------------------------------------------------------------
# 6. Persist WIKI_REPO (a convenience — agent prompts resolve it themselves)
#
# Only in an actual cloud sandbox. On a workstation the default
# ~/Workspaces/wiki already resolves, and pinning an env var there just creates
# a stale override the next time the wiki moves.
# -----------------------------------------------------------------------------
export WIKI_REPO="$WIKI_REPO_PATH"
if [[ -d "$HOME/Workspaces/AgentArchitect" ]]; then
  echo "==> Workstation detected (AgentArchitect present) — not pinning WIKI_REPO in shell rc"
else
  for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
    [[ -f "$rc" ]] || continue
    if ! grep -q 'export WIKI_REPO=' "$rc" 2>/dev/null; then
      echo "export WIKI_REPO=\"$WIKI_REPO_PATH\"" >> "$rc"
      echo "==> Persisted WIKI_REPO in $(basename "$rc")"
    fi
  done
fi

# -----------------------------------------------------------------------------
# 7. Capability probe (what bash can actually see — MCP presence cannot be)
# -----------------------------------------------------------------------------
echo
echo "==> Capability probe"
command -v codex >/dev/null 2>&1 \
  && echo "    [have] codex — outside-voice review uses it" \
  || echo "    [none] codex — outside-voice review falls back to a subagent reviewer"
if command -v curl >/dev/null 2>&1; then
  # Any HTTP response means we have egress and the service answered. Don't use
  # -f: the root path legitimately returns 404/405 and that is still "reachable".
  rag_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 \
    https://rag-api-934267405367.us-central1.run.app 2>/dev/null || echo 000)
  if [[ "$rag_code" != "000" ]]; then
    echo "    [have] Cloud Run RAG API reachable (HTTP $rag_code) — rag-search works"
  else
    echo "    [none] Cloud Run RAG API unreachable — document search degraded"
  fi
fi
echo "    [note] MCP server availability cannot be probed from bash."
echo "           See wiki spine/infrastructure/cloud-mode-capabilities.md."

# -----------------------------------------------------------------------------
# 8. Report
# -----------------------------------------------------------------------------
# Flatten the manifest before matching — it is pretty-printed, so the first
# array element sits on its own line and a single-line regex misses it.
manifest_flat=$(tr -d '\n' < "$REPO_ROOT/.agentarchitect.json" 2>/dev/null || true)
team=$(printf '%s' "$manifest_flat" | sed -n 's/.*"team"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
skill=$(printf '%s' "$manifest_flat" | sed -n 's/.*"skills"[[:space:]]*:[[:space:]]*\[[[:space:]]*"\([^"]*\)".*/\1/p')

echo
echo "==> Bootstrap complete"
echo "    WIKI_REPO:  $WIKI_REPO"
echo "    Agents:     $agent_count    Skills: $skill_count"
if [[ "$push_ok" -eq 1 ]]; then
  echo "    Wiki:       read + write + push"
else
  echo "    Wiki:       READ-ONLY — wiki edits cannot be pushed from this sandbox."
  echo "                Work normally, but surface any wiki change as a markdown"
  echo "                block for Nick to apply on the Mac. Do not pretend it saved."
fi
echo
echo "Next:"
echo "    • Start the team:  /${skill:-<team-skill>}${team:+   (team: $team)}"
echo "    • Before you close the session: /wrap  — MANDATORY here."
echo "      A cloud sandbox is ephemeral; an uncommitted tree is deleted work."
echo

[[ "$push_ok" -eq 1 ]] || exit 4
exit 0
