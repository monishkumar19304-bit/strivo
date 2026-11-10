#!/usr/bin/env python3
"""PostToolUse on the Agent tool: when an agent in a team's feedback loop
returns, tell the orchestrator that the feedback entry is due.

Why: the team skill already says to log feedback after such an agent returns,
but that is an instruction in a long prompt, and it gets skipped. This puts a
one-line reminder into the orchestrator's context at the moment the agent
returns, naming the file to append to.

Why PostToolUse and not SubagentStop: tested on 2026-10-06, context added by a
SubagentStop hook did not reach the parent session; context added by a
PostToolUse hook on the Agent tool did. The cost is that an agent launched in
the background is not covered, because its tool call returns at launch.

It does not write the entry itself. Judging how a run went (what worked, what
struggled, whether the user had to correct it) is the orchestrator's job; the
hook only makes sure the question is asked.

Which agents are in a feedback loop comes from the generated team skills:
the generator writes `<!-- aa-feedback: {"agent": ..., "id": ..., "path": ...} -->`
into a team skill for each one (see generate-agents.js). No per-repo config.

Factory-managed: edit templates/portable/hooks/ in AgentArchitect, then `aa sync`.
Never blocks and never fails a session: every error path exits 0 silently.
"""
import glob
import json
import os
import re
import sys

MARK = re.compile(r"<!-- aa-feedback: (\{.*?\}) -->")


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    event = data.get("hook_event_name") or "PostToolUse"
    if event == "PostToolUse":
        tool_input = data.get("tool_input") or {}
        agent = str(tool_input.get("subagent_type") or "")
        response = data.get("tool_response")
        # A background launch returns at once; the agent has not done anything yet.
        if isinstance(response, dict) and response.get("status") == "async_launched":
            return
    else:
        agent = str(data.get("agent_type") or "")
    if not agent:
        return
    root = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    for skill in glob.glob(os.path.join(root, ".claude", "skills", "*", "SKILL.md")):
        try:
            with open(skill, encoding="utf-8") as f:
                text = f.read()
        except Exception:
            continue
        for match in MARK.finditer(text):
            try:
                entry = json.loads(match.group(1))
            except Exception:
                continue
            if agent not in (entry.get("agent"), entry.get("id")):
                continue
            note = (
                f"Feedback capture is due: {entry.get('agent', agent)} just finished. Before you answer "
                f"the user, append one JSON line about this run to {entry.get('path')} (schema and rubric: "
                f"\"Post-Task Feedback Capture\" in the team skill). If that folder does not exist in this "
                f"session, put the same JSON under a Feedback: line in the wiki session log."
            )
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": event,
                "additionalContext": note}}))
            return


try:
    main()
except Exception:
    pass
sys.exit(0)
