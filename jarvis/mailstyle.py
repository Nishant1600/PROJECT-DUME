"""Your email writing voice (README roadmap: "replies drafted in your own voice").

JARVIS keeps a short note on how *you* write email — greeting, sign-off, formality, typical
length, stock phrases — in memory/mailstyle.md, and feeds it into its context so drafted
replies sound like you, not like a generic assistant. Every draft is still shown before send;
JARVIS never sends without your yes.

The note is learned, not hand-written: the brain reads a handful of your recent *sent*
messages through the Gmail connector and distils them. This module just owns the file (seed,
read, cap) and a tiny CLI the brain can pipe the distilled note into.

    python -m jarvis.mailstyle show
    echo "<distilled note>" | python -m jarvis.mailstyle set
"""
from __future__ import annotations

import re
import sys

from .config import MEMORY_DIR

MAILSTYLE_FILE = MEMORY_DIR / "mailstyle.md"
MAX_MAILSTYLE_CHARS = 1200

SEED = """<!--
  How I write email, learned from my own sent messages, so JARVIS drafts in my voice.
  JARVIS fills this in the first time it drafts a reply (it reads a few of your recent sent
  emails and distils them). You can edit it freely. Keep it short.
-->

# My email voice
- (not learned yet — JARVIS will fill this in from your recent sent mail)
"""


def ensure() -> None:
    if not MAILSTYLE_FILE.exists():
        MAILSTYLE_FILE.parent.mkdir(parents=True, exist_ok=True)
        MAILSTYLE_FILE.write_text(SEED.strip() + "\n", encoding="utf-8")


def _read() -> str:
    try:
        return MAILSTYLE_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""


def is_learned() -> bool:
    text = re.sub(r"<!--.*?-->", "", _read(), flags=re.S)
    return "not learned yet" not in text.lower() and len(text.strip()) > 40


def style_text() -> str:
    """The learned voice note, comments stripped and capped, for context injection."""
    text = re.sub(r"<!--.*?-->", "", _read(), flags=re.S)
    text = re.sub(r"(?m)^\s*(?:<!--|-->).*$", "", text).strip()
    if "not learned yet" in text.lower():
        return ""
    if len(text) > MAX_MAILSTYLE_CHARS:
        text = text[:MAX_MAILSTYLE_CHARS].rsplit("\n", 1)[0].rstrip() + "\n… (truncated)"
    return text


def set_style(note: str) -> bool:
    note = (note or "").strip()
    if len(note) < 20:
        return False
    if not note.lower().lstrip().startswith("#"):
        note = "# My email voice\n" + note
    MAILSTYLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    MAILSTYLE_FILE.write_text(note[: MAX_MAILSTYLE_CHARS + 400] + "\n", encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "show"
    ensure()
    if cmd == "set":
        note = sys.stdin.read()
        print("Saved your email voice." if set_style(note)
              else "Note too short — give me more to go on.")
        return 0
    # show
    text = style_text()
    print(text if text else "(email voice not learned yet)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
