"""Live notetaker (#57): transcribe and summarise a meeting *as it happens*.

Unlike lecture capture (which transcribes a finished recording), this runs live: while a
note session is active, everything JARVIS hears in the room is appended, timestamped, to
a running transcript file under runtime/notes/. When you say "stop taking notes", the
brain reads that transcript and produces a summary with decisions and action items
(saved to Notion when connected).

The capture itself reuses the speech-to-text JARVIS already runs, so there's no extra
model and nothing leaves the machine until you ask for the summary.
"""
from __future__ import annotations

import datetime
import re
import threading

from .config import RUNTIME_DIR

NOTES_DIR = RUNTIME_DIR / "notes"

_START = re.compile(
    r"^(?:/notes?|start (?:taking |the )?notes?|take notes|note(?:\s+this)?(?:\s+(?:meeting|call|lecture))?|"
    r"minute this(?:\s+meeting)?|start the notetaker|take minutes)\W*$", re.I)
_STOP = re.compile(
    r"^(?:/endnotes?|stop (?:taking )?notes?|end (?:the )?notes?|finish (?:the )?notes?|"
    r"wrap up (?:the )?notes?|stop the notetaker|done taking notes)\W*$", re.I)


def is_start_trigger(text: str) -> bool:
    return bool(_START.match((text or "").strip()))


def is_stop_trigger(text: str) -> bool:
    return bool(_STOP.match((text or "").strip()))


def fmt_timestamp(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class NoteSession:
    def __init__(self, title: str = "", start: datetime.datetime | None = None):
        self.started = start or datetime.datetime.now()
        self.title = title.strip() or f"Notes {self.started:%Y-%m-%d %H:%M}"
        self.lines: list[str] = []
        self._lock = threading.Lock()
        stamp = self.started.strftime("%Y%m%d-%H%M%S")
        self.path = NOTES_DIR / f"{stamp}.md"

    def append(self, text: str, at: datetime.datetime | None = None) -> None:
        text = " ".join((text or "").split())
        if not text:
            return
        at = at or datetime.datetime.now()
        elapsed = (at - self.started).total_seconds()
        with self._lock:
            self.lines.append(f"[{fmt_timestamp(elapsed)}] {text}")

    def transcript(self) -> str:
        with self._lock:
            return "\n".join(self.lines)

    def save(self) -> str:
        NOTES_DIR.mkdir(parents=True, exist_ok=True)
        header = f"# {self.title}\n\n_Started {self.started:%Y-%m-%d %H:%M}_\n\n"
        self.path.write_text(header + self.transcript() + "\n", encoding="utf-8")
        return str(self.path)


# ----------------------------------------------------------------- in-process session

_active: NoteSession | None = None
_active_lock = threading.Lock()


def start(title: str = "") -> NoteSession:
    global _active
    with _active_lock:
        _active = NoteSession(title)
        _active.save()   # create the file now so it exists even if empty
    return _active


def active() -> NoteSession | None:
    return _active


def feed(text: str) -> bool:
    """Append an utterance to the active session. Returns True if a session is running."""
    sess = _active
    if sess is None:
        return False
    sess.append(text)
    sess.save()
    return True


def stop() -> str | None:
    """Finalise the active session and return its transcript path (or None if none)."""
    global _active
    with _active_lock:
        sess = _active
        _active = None
    if sess is None:
        return None
    return sess.save()


def summary_prompt(path: str, cfg: dict | None = None) -> str:
    integ = (cfg or {}).get("integrations", {}) if cfg else {}
    notion = (" Then save it as a Notion page in my meeting-notes space."
              if integ.get("use_my_connectors") else "")
    return (
        "I've finished a meeting you were taking notes on. Read the timestamped transcript at "
        f"`{path}` (use the Read tool), then give me, spoken and brief: a two or three sentence "
        "summary of what was discussed, the key decisions, and a clear list of action items with "
        "who owns each if it's said. Don't read the transcript back verbatim." + notion
    )
