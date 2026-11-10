#!/usr/bin/env python3
"""PreToolUse guard for anything that reaches another person's inbox.

Two kinds of tool call come here:

  Mail      send / reply / forward. Blocked unless every recipient is one of
            Nick's own addresses. Drafts are not matched.
  Sharing   calendar events with guests, and sharing a Drive file or a Google
            Doc. Each of these emails the other person an invitation or a
            notice, so they follow the same rule: blocked unless every guest
            or share target is one of Nick's own addresses. An event with no
            guests, or a call that names nobody, is allowed.

The mail half is the repo-level copy of ~/.claude/hooks/email-send-guard.py, so
cloud and iOS sessions (which never see user-level settings on the Mac) enforce
"draft, never send" too. On the Mac both copies run and agree.

Not covered, because the tool input does not say who gets notified: an RSVP,
and editing or deleting an event that already has guests.

Factory-managed: edit templates/portable/hooks/ in AgentArchitect, then `aa sync`.

The allowlist is stored as SHA-256 hashes, not addresses, so this file can be
committed in a public repo without publishing anyone's email. To add an
address:  python3 -c "import hashlib; print(hashlib.sha256(b'you@example.com').hexdigest())"
"""
import hashlib
import json
import re
import sys

# SHA-256 of each of Nick's own addresses, lowercased.
ALLOWED_SHA256 = {
    "f9439f8b60df1d99c8d2ba8ea9aef367fb57db973bdbe767d6e5428a6b5187be",
    "0fe84d6628003e7ae8b62ca7e7ffb36f32727cc8cb2690e546bc4a17ec508f00",
}
RECIPIENT_KEYS = {"to", "cc", "bcc", "recipients", "recipient", "to_recipients",
                  "cc_recipients", "bcc_recipients"}
# Where a calendar or sharing tool names the people it will notify. Keys are
# compared lowercased with underscores removed, so emailAddress, email_address
# and addedAttendeeEmails all match.
SHARE_KEYS = {"emailaddress", "emailaddresses", "attendees", "attendeeemails",
              "addedattendees", "addedattendeeemails", "guests", "invitees",
              "sharewith", "sharedwith", "users"}
MAIL_TOOL_RE = re.compile(r"^mcp__.*[Mm]ail.*__(send|reply|forward)")
CALENDAR_TOOL_RE = re.compile(r"(?i)calendar|_event")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def norm(key):
    return key.lower().replace("_", "")


def collect(node, keys, out):
    if isinstance(node, dict):
        for k, v in node.items():
            if norm(k) in keys:
                out.extend(m.lower() for m in EMAIL_RE.findall(json.dumps(v)))
            else:
                collect(v, keys, out)
    elif isinstance(node, list):
        for v in node:
            collect(v, keys, out)


def outsiders(addresses):
    return sorted(set(a for a in addresses
                      if hashlib.sha256(a.encode()).hexdigest() not in ALLOWED_SHA256))


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason}}))
    sys.exit(0)


tool_name, tool_input = "", {}
try:
    data = json.load(sys.stdin)
    tool_name = str(data.get("tool_name") or "")
    tool_input = data.get("tool_input", {})
except Exception:
    deny("Email send guard: could not parse tool input; blocking to be safe.")

# Anything that is not recognisably a sharing or calendar call gets the strict
# mail rule, so an unknown tool name can only be blocked more, never less.
is_mail = bool(MAIL_TOOL_RE.search(tool_name)) or not re.search(r"(?i)share|calendar|_event", tool_name)

if is_mail:
    recipients = []
    collect(tool_input, {norm(k) for k in RECIPIENT_KEYS}, recipients)
    if not recipients:
        deny("Email send guard: no explicit recipients to verify (replies/forwards "
             "to a thread are blocked). Create a draft instead; Nick sends it himself.")
    bad = outsiders(recipients)
    if bad:
        deny("Email send guard: sending is only allowed to Nick's own addresses. "
             f"Blocked recipients: {', '.join(bad)}. Create a draft instead.")
    sys.exit(0)

people = []
collect(tool_input, SHARE_KEYS | {norm(k) for k in RECIPIENT_KEYS}, people)
bad = outsiders(people)
if bad:
    if CALENDAR_TOOL_RE.search(tool_name):
        deny("Calendar guard: adding guests sends them an invitation, and only "
             f"Nick does that. Blocked guests: {', '.join(bad)}. Create or update "
             "the event without guests and tell Nick who to invite.")
    deny("Sharing guard: sharing a file notifies the other person, and only Nick "
         f"does that. Blocked: {', '.join(bad)}. Give Nick the link and the "
         "address so he can share it himself.")
sys.exit(0)
