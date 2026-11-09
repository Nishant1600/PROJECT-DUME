"""Smart calendar scheduling (#60): "find me an hour tomorrow for revision".

A one-off scheduling request (distinct from recurring routines in jarvis.schedules)
expands into an instruction for the brain to find a free slot with the Calendar
connector's suggest_time, propose it, and create the event only after I confirm.

The module does the light NL extraction (what, how long, when-ish) so the instruction
is concrete; the brain does the calendar work and always confirms before creating.
"""
from __future__ import annotations

import re

# recurring phrasing belongs to jarvis.schedules, not here
_RECURRING = re.compile(r"\b(every|each|daily|weekly|hourly|weekdays?|weekends?)\b", re.I)

_TRIGGER = re.compile(
    r"\b(?:find (?:me )?(?:some )?time|find (?:me )?(?:an?|\d+)\s*(?:hour|hr|minute|min)|"
    r"block (?:out )?(?:some )?time|book (?:me )?(?:a|some)?\s*(?:slot|time)|"
    r"schedule (?:a|an|some)\b|put .* on my calendar|add .* to my calendar|"
    r"set up (?:a )?meeting|make time)\b", re.I)

_DUR = re.compile(r"\b(\d+(?:\.\d+)?)\s*(hours?|hrs?|minutes?|mins?)\b", re.I)
_DUR_WORD = re.compile(r"\b(?:an?|half an?)\s+(hour|hr)\b", re.I)
_WHEN = re.compile(
    r"\b(today|tomorrow|tonight|this (?:morning|afternoon|evening|week|weekend)|"
    r"next week|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I)
_ACTIVITY = re.compile(r"\b(?:for|to|about)\s+(.+?)(?:\s+(?:on|at|this|tomorrow|today|tonight|next)\b|[.?!]|$)", re.I)


def is_trigger(text: str) -> bool:
    t = (text or "").strip()
    if _RECURRING.search(t):
        return False
    return bool(_TRIGGER.search(t))


def extract(text: str) -> dict:
    """Best-effort parse of duration / when / activity for a concrete instruction."""
    t = " ".join((text or "").split())
    low = t.lower()

    minutes = None
    m = _DUR.search(low)
    if m:
        n = float(m.group(1))
        minutes = int(n * 60) if m.group(2).startswith(("hour", "hr")) else int(n)
    elif _DUR_WORD.search(low):
        minutes = 30 if "half" in low else 60

    when = None
    m = _WHEN.search(low)
    if m:
        when = m.group(1)

    activity = None
    m = _ACTIVITY.search(t)
    if m:
        activity = m.group(1).strip(" .?!,")

    return {"minutes": minutes, "when": when, "activity": activity}


def scheduling_prompt(text: str, cfg: dict | None = None) -> str:
    info = extract(text)
    connected = ((cfg or {}).get("integrations", {}) or {}).get("use_my_connectors")
    dur = f"{info['minutes']} minutes" if info["minutes"] else "a suitable length"
    when = info["when"] or "soon"
    what = info["activity"] or "this"
    if not connected:
        return ("The user wants me to find calendar time, but the Calendar connector isn't "
                "enabled. Tell them to connect Google Calendar via /mcp and set "
                "integrations.use_my_connectors. Say this in one line.")
    return (
        f"Find me time for: {what}. I'm after about {dur}, {when}.\n"
        "Use the calendar's suggest_time (or list my events and find a genuinely free gap) to "
        "propose one specific slot — day and clock time. Tell me the slot and ask me to confirm. "
        "Only after I say yes, create the event (title it for the activity). Never create or move "
        "anything on my calendar without my explicit yes."
    )
