"""Focus mode for JARVIS (README roadmap: "one word hushes every nudge while you're deep in work").

Say "focus" (or "do not disturb") and JARVIS goes quiet: no proactive nudges, no deadline
heads-up, no commit prompts. Reminders you set are held, not lost — they fire the moment you
say "focus off" / "I'm back". It never silences a direct reply to something you ask.

State is a flag file under runtime/ so the background nudge thread and the command handler
(different threads) always agree on it.
"""
from __future__ import annotations

from .config import RUNTIME_DIR

FOCUS_FILE = RUNTIME_DIR / "FOCUS"


def on() -> None:
    FOCUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    FOCUS_FILE.touch()


def off() -> None:
    FOCUS_FILE.unlink(missing_ok=True)


def active() -> bool:
    return FOCUS_FILE.exists()
