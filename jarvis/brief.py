"""The spoken morning brief (README roadmap: "a spoken morning brief").

One consistent brief, whether it fires on startup (config integrations.startup_brief) or
you ask for it by voice ("brief me", "good morning", "what's my day look like"). It leans on
your connectors for mail / calendar / Notion and on the local deadline tracker, and it's
written to be short and spoken, never a wall of text.

Expanding a trigger phrase into the full instruction (rather than making it a local canned
reply) means the brain actually assembles the brief from live data each time.
"""
from __future__ import annotations

import re

_TRIGGER = re.compile(
    r"^(?:/brief|brief me|brief|good morning(?: jarvis)?|morning brief|"
    r"(?:what(?:'s| is)?|how(?:'s| is)?)\s+(?:my\s+)?day(?:\s+look(?:ing)?(?:\s+like)?)?|"
    r"what(?:'s| is)?\s+on\s+today|my day)\W*$", re.I)

# The user's original "status" idea: important mail grouped by who it's from.
_STATUS_TRIGGER = re.compile(
    r"^(?:/mailstatus|(?:give me (?:a|my) )?status(?: update| check| report)?|"
    r"what(?:'s| is)?\s+(?:the\s+|my\s+)?status|any(?:thing)? important)\W*$", re.I)


def is_brief_trigger(text: str) -> bool:
    return bool(_TRIGGER.match((text or "").strip()))


def is_status_trigger(text: str) -> bool:
    return bool(_STATUS_TRIGGER.match((text or "").strip()))


def status_prompt(cfg: dict | None = None) -> str:
    """The instruction behind a spoken 'status' — important mail, grouped by sender/company."""
    return (
        "Give me my status: check my important unread email and tell me what actually needs me. "
        "Group it by who it's from — the company, service or person — one short spoken line each "
        "(for example 'two from your bank about a statement, one from a recruiter at Acme'). Put "
        "the things that matter first and skip newsletters, promotions and automated noise. Keep "
        "it to a few spoken sentences, don't read anything out verbatim, and then I may ask you to "
        "'read the one from X'. If the Gmail connector isn't available, say so briefly."
    )


def brief_prompt(cfg: dict | None = None) -> str:
    """The instruction JARVIS runs to produce the morning brief."""
    return (
        "Give me my brief for today. Pull together, using whatever is available:\n"
        "- important unread email (group by who it's from; skip newsletters and noise),\n"
        "- what's on my calendar today, flagging any clashes,\n"
        "- Notion tasks or pages due,\n"
        "- deadlines I'm tracking — run `python -m jarvis.deadlines due --days 7` via Bash and "
        "include anything overdue or due soon.\n"
        "Then give it to me as two or three short spoken sentences — the few things that actually "
        "need my attention, most urgent first. No lists, no reading things out verbatim. If a "
        "connector or tool isn't available, just skip that part quietly and don't mention it."
    )
