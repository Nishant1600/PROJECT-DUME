"""Proactive nudges for JARVIS (feature #16).

Two kinds, deliberately restrained so JARVIS is never chatty:

  * Reminders you set ("remind me to call mum at 6pm") always fire - you asked for them.
  * Unsolicited nudges (e.g. "you have uncommitted changes - want me to commit?") fire
    only if you turn them on (config: nudges.enabled), and even then are rate-limited,
    happen only while JARVIS is idle, and stay quiet outside your waking hours.

Nothing here acts on its own; a nudge just speaks a short line and shows it on the HUD.
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import threading
import time
from typing import Callable

from .config import RUNTIME_DIR

log = logging.getLogger(__name__)

REMINDERS_FILE = RUNTIME_DIR / "reminders.json"


# --------------------------------------------------------------------------- when-parsing

def parse_when(text: str, now: datetime.datetime | None = None) -> tuple[str, float] | None:
    """Pull a time out of reminder text. Returns (clean_text, epoch_seconds) or None.

    Understands "in N minutes/hours" and "at 6[:30][pm]" (optionally "tomorrow at ...").
    """
    now = now or datetime.datetime.now()
    t = " ".join((text or "").split())
    low = t.lower()

    m = re.search(r"\bin\s+(\d+)\s*(minutes?|mins?|hours?|hrs?)\b", low)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta = datetime.timedelta(hours=n) if unit.startswith(("hour", "hr")) else datetime.timedelta(minutes=n)
        clean = re.sub(r"\bin\s+\d+\s*(?:minutes?|mins?|hours?|hrs?)\b", "", t, flags=re.I)
        return _clean(clean), (now + delta).timestamp()

    m = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", low)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2) or 0)
        ap = m.group(3)
        if ap == "pm" and hh < 12:
            hh += 12
        if ap == "am" and hh == 12:
            hh = 0
        if hh > 23 or mm > 59:
            return None
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if "tomorrow" in low:
            target += datetime.timedelta(days=1)
        elif target <= now:
            target += datetime.timedelta(days=1)   # the next time that clock time comes round
        clean = re.sub(r"\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b", "", t, flags=re.I)
        clean = re.sub(r"\btomorrow\b", "", clean, flags=re.I)
        return _clean(clean), target.timestamp()

    return None


def _clean(text: str) -> str:
    text = " ".join(text.split()).strip(" ,.")
    text = re.sub(r"^(?:to|that|about)\s+", "", text, flags=re.I)   # "to call mum" -> "call mum"
    return text.strip()


# --------------------------------------------------------------------------- reminders store

class Reminders:
    def _load(self) -> list[dict]:
        try:
            return json.loads(REMINDERS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _save(self, items: list[dict]) -> None:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        REMINDERS_FILE.write_text(json.dumps(items, indent=2), encoding="utf-8")

    def add(self, text: str, when_ts: float) -> None:
        items = self._load()
        items.append({"text": text, "ts": float(when_ts), "set": time.time()})
        self._save(items)

    def all(self) -> list[dict]:
        return sorted(self._load(), key=lambda r: r["ts"])

    def due(self, now_ts: float | None = None) -> list[dict]:
        """Return reminders whose time has come and remove them from the store."""
        now_ts = now_ts if now_ts is not None else time.time()
        items = self._load()
        fired = [r for r in items if r["ts"] <= now_ts]
        if fired:
            self._save([r for r in items if r["ts"] > now_ts])
        return fired


# --------------------------------------------------------------------------- engine

class NudgeEngine:
    """Background thread that speaks due reminders and (if enabled) occasional nudges."""

    def __init__(self, cfg: dict, bus, speaker, sessions, is_idle: Callable[[], bool],
                 address_as: str = "sir"):
        self.cfg = (cfg.get("nudges") or {})
        self.bus = bus
        self.speaker = speaker
        self.sessions = sessions
        self.is_idle = is_idle
        self.address_as = address_as
        self.reminders = Reminders()
        self._stop = threading.Event()
        self._last_nudge = 0.0
        self._last_commit_nudge = 0.0

    # -- gating helpers (pure, so they're easy to test)
    def _in_quiet_hours(self, now: datetime.datetime) -> bool:
        start = int(self.cfg.get("quiet_after", 22))   # e.g. don't nudge after 22:00
        end = int(self.cfg.get("quiet_before", 8))      # ...or before 08:00
        h = now.hour
        return h >= start or h < end

    def _check_reminders(self, now_ts: float | None = None) -> list[str]:
        who = self.address_as or "sir"
        return [f"{who.capitalize()}, a reminder: {r['text']}." for r in self.reminders.due(now_ts)]

    def _maybe_commit_nudge(self, now_ts: float) -> str | None:
        if not self.cfg.get("enabled"):
            return None
        if now_ts - self._last_commit_nudge < 3 * 3600:   # at most once every few hours
            return None
        try:
            from .repo import status
            s = status(str(self.sessions.cwd))
        except Exception:
            return None
        if s.get("repo") and s.get("dirty_count", 0) >= 3:
            self._last_commit_nudge = now_ts
            name = (s.get("branch") or "your project")
            return f"You have {s['dirty_count']} uncommitted changes on {name}. Shall I commit them?"
        return None

    # -- the loop
    def start(self) -> "NudgeEngine":
        threading.Thread(target=self._loop, name="jarvis-nudges", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _speak(self, message: str) -> None:
        from . import bus as B
        self.bus.publish(B.NOTICE, text=message, level="info")
        if self.speaker:
            self.speaker.say(message)
        self._last_nudge = time.time()

    def _loop(self) -> None:
        min_gap = int(self.cfg.get("min_gap_minutes", 20)) * 60
        while not self._stop.wait(30):
            try:
                if not self.is_idle():
                    continue
                now = datetime.datetime.now()
                now_ts = now.timestamp()
                # reminders the user set always fire (they asked), ignoring quiet hours
                for msg in self._check_reminders(now_ts):
                    self._speak(msg)
                    time.sleep(2)
                # unsolicited nudges: opt-in, spaced out, and only in waking hours
                if now_ts - self._last_nudge < min_gap or self._in_quiet_hours(now):
                    continue
                msg = self._maybe_commit_nudge(now_ts)
                if msg:
                    self._speak(msg)
            except Exception:
                log.exception("nudge loop error")
