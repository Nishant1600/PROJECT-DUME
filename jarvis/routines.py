"""Named routines for JARVIS (feature #18).

A routine is a saved instruction you trigger by one phrase:

    "save routine morning: brief me on mail and calendar, then open my DUME project"
    ...later...
    "run the morning routine"   ->  JARVIS carries out the saved instruction

Stored as JSON in runtime/routines.json. Management ("save/forget/list routine")
is handled as a local command; triggering expands the phrase into its instruction,
which is then sent to the brain like any request.
"""
from __future__ import annotations

import json
import re

from .config import RUNTIME_DIR

ROUTINES_FILE = RUNTIME_DIR / "routines.json"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def _load() -> dict:
    try:
        return json.loads(ROUTINES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    ROUTINES_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


class Routines:
    def names(self) -> list[str]:
        return [v["name"] for v in sorted(_load().values(), key=lambda v: v["name"].lower())]

    def get(self, name: str) -> str | None:
        v = _load().get(_norm(name))
        return v["prompt"] if v else None

    def save(self, name: str, prompt: str) -> str | None:
        key = _norm(name)
        if not key or not prompt.strip():
            return None
        data = _load()
        data[key] = {"name": name.strip(), "prompt": " ".join(prompt.split())}
        _save(data)
        return name.strip()

    def delete(self, name: str) -> bool:
        data = _load()
        key = _norm(name)
        if key in data:
            del data[key]
            _save(data)
            return True
        return False

    def match(self, text: str) -> tuple[str, str] | None:
        """If the utterance triggers a routine, return (display_name, instruction).

        Conservative on purpose, so normal requests are never hijacked: it fires only
        on an exact routine name, "run/start/do <name>", or "<name> routine"."""
        t = _norm(text)
        if not t:
            return None
        data = _load()
        if t in data:
            return data[t]["name"], data[t]["prompt"]
        m = re.match(r"(?:run|start|do|execute|begin)(?:\s+my|\s+the)?\s+(.+?)(?:\s+routine)?$", t)
        if m and _norm(m.group(1)) in data:
            v = data[_norm(m.group(1))]
            return v["name"], v["prompt"]
        m = re.match(r"(.+?)\s+routine$", t)
        if m and _norm(m.group(1)) in data:
            v = data[_norm(m.group(1))]
            return v["name"], v["prompt"]
        return None


# management-command patterns (used by commands.py) -------------------------
SAVE_RE = re.compile(
    r"^(?:/routine\s+|(?:save|create|make|add|define)(?: a| new)? routine\s+)"
    r"(?:called |named )?(?P<name>.+?)\s*(?:[:=]|\bthat\b|\bto\b|\s-\s)\s*(?P<prompt>.+)$", re.I)
DELETE_RE = re.compile(r"^(?:forget|delete|remove|drop) (?:the )?routine (?:called |named )?(?P<name>.+?)\W*$", re.I)
LIST_RE = re.compile(r"^(?:list|show|what are)\s+(?:my\s+)?routines\W*$", re.I)
