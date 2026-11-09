"""Daily & weekly recap (#59): what got done, what's still open.

A trigger phrase ("recap my day", "weekly recap", "wrap up") expands into an instruction
for the brain to pull together what happened — from the running memory summary, tracked
deadlines, and (when connected) sent mail, calendar and Notion — and give it back as a
short spoken recap plus a written page saved to Notion. Expanding the phrase means the
recap is assembled from live state each time, not canned.
"""
from __future__ import annotations

import re

_TRIGGER = re.compile(
    r"^(?:/recap|recap(?:\s+(?:my\s+)?(?:day|week))?|daily recap|weekly recap|"
    r"wrap up(?:\s+(?:my\s+)?(?:day|week))?|end of (?:the )?day|end of (?:the )?week|"
    r"what did i (?:get done|do)(?:\s+(?:today|this week))?|how was my (?:day|week))\W*$", re.I)


def is_trigger(text: str) -> bool:
    return bool(_TRIGGER.match((text or "").strip()))


def span_of(text: str) -> str:
    """'week' if the request is weekly, else 'day'."""
    return "week" if re.search(r"\bweek(?:ly)?\b", (text or "").lower()) else "day"


def recap_prompt(cfg: dict | None = None, span: str = "day") -> str:
    integ = (cfg or {}).get("integrations", {}) if cfg else {}
    connected = integ.get("use_my_connectors")
    window = "today" if span == "day" else "this week"
    sources = ["the running memory of what we worked on",
               "deadlines I'm tracking (run `python -m jarvis.deadlines list` via Bash)"]
    if connected:
        sources += ["mail I sent and received " + window,
                    "my calendar for " + window,
                    "Notion tasks I completed or left open"]
    save = (" Then write it up as a Notion page titled with the date so I have a log."
            if connected else "")
    return (
        f"Give me my recap for {window}. Pull together, from whatever is available:\n- "
        + "\n- ".join(sources) + "\n"
        "Tell me, spoken and brief: what actually got done, then what's still open or carrying "
        "over. Two or three sentences for each, most important first — no lists read aloud, no "
        f"filler. If a source isn't available, skip it quietly.{save}"
    )
