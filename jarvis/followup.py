"""Follow-up memory (#70): resolve "read the one from the bank" without re-asking.

After a status briefing ("two from your bank, one from a recruiter…") JARVIS remembers
the senders it just named, briefly. A referential follow-up — "read the one from the
bank", "the second one", "reply to the recruiter one" — is then matched against that
short-term context and expanded into a precise instruction, so the brain acts on the
right item instead of asking again.

The brain registers the context after a briefing:
    python -m jarvis.followup set mail "the bank,a recruiter at Acme,your university"
and it expires after a few minutes so stale references never misfire.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time

from .config import RUNTIME_DIR

FOLLOWUP_FILE = RUNTIME_DIR / "followup.json"
TTL_SECONDS = 600        # context is only "fresh" for a few minutes

_ORDINALS = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2,
             "fourth": 3, "4th": 3, "fifth": 4, "5th": 4, "last": -1}

_REFERENTIAL = re.compile(
    r"\b(?:the\s+(?:first|second|third|fourth|fifth|last|1st|2nd|3rd|4th|5th)\s+one"
    r"|(?:read|open|show|summari[sz]e|reply to|respond to|answer|delete)\b.*?\b(?:one|email|message|from)\b"
    r"|that one|this one|the\s+.+?\s+one)\b", re.I)


def is_followup(text: str) -> bool:
    return bool(_REFERENTIAL.search((text or "").strip()))


# --------------------------------------------------------------------------- store

def set_context(kind: str, items: list[str]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    clean = [i.strip() for i in items if i and i.strip()]
    FOLLOWUP_FILE.write_text(json.dumps({"kind": kind, "items": clean, "ts": time.time()}, indent=2),
                             encoding="utf-8")


def get_context(now: float | None = None) -> dict | None:
    now = now if now is not None else time.time()
    try:
        data = json.loads(FOLLOWUP_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not data.get("items") or now - float(data.get("ts", 0)) > TTL_SECONDS:
        return None
    return data


def clear() -> None:
    FOLLOWUP_FILE.unlink(missing_ok=True)


# --------------------------------------------------------------------------- resolution

def _action(low: str) -> str:
    if re.search(r"\b(reply|respond|answer|write back)\b", low):
        return "reply"
    if re.search(r"\b(delete|trash|archive)\b", low):
        return "delete"
    return "read"


def _match_item(text: str, items: list[str]) -> str | None:
    low = text.lower()
    # ordinal: "the second one"
    m = re.search(r"\b(first|second|third|fourth|fifth|last|1st|2nd|3rd|4th|5th)\b", low)
    if m and items:
        try:
            return items[_ORDINALS[m.group(1)]]
        except IndexError:
            return None
    # "from X" or "the X one"
    token = None
    m = re.search(r"\bfrom\s+(?:the\s+)?(.+?)(?:\s+one|\s+email|\s+message|[.?!]|$)", low)
    if m:
        token = m.group(1)
    if not token:
        m = re.search(r"\bthe\s+(.+?)\s+one\b", low)
        if m:
            token = m.group(1)
    if not token:
        return None
    token = re.sub(r"^(?:that|this|the)\s+", "", token.strip()).strip()
    words = [w for w in re.findall(r"[a-z0-9]+", token) if len(w) > 2]
    for it in items:
        il = it.lower()
        if token and token in il:
            return it
        if any(w in il for w in words):
            return it
    return None


def _instruction(action: str, item: str, kind: str) -> str:
    if kind == "mail":
        if action == "reply":
            return (f"Open the most recent important email from {item} and draft a reply in my "
                    "voice. Show it to me before sending — do not send without my explicit yes.")
        if action == "delete":
            return (f"Find the most recent email from {item}. Tell me what it is and ask me to "
                    "confirm before trashing it — never delete mail without my yes.")
        return (f"Open the most recent important email from {item}, read me a short summary of what "
                "it says, then ask if I'd like to reply.")
    return f"{action.capitalize()} the item relating to {item}."


def resolve(text: str, context: dict | None) -> tuple[str, str] | None:
    """Return (item, instruction) if the text references something in context, else None."""
    if not context or not context.get("items"):
        return None
    item = _match_item(text, context["items"])
    if not item:
        return None
    return item, _instruction(_action(text.lower()), item, context.get("kind", ""))


def resolve_now(text: str, now: float | None = None) -> tuple[str, str] | None:
    return resolve(text, get_context(now))


# --------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jarvis.followup", description="Short-term follow-up context")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("set"); s.add_argument("kind"); s.add_argument("items")
    sub.add_parser("show")
    sub.add_parser("clear")
    r = sub.add_parser("resolve"); r.add_argument("text", nargs="+")
    args = ap.parse_args(argv)

    if args.cmd == "set":
        set_context(args.kind, [x for x in re.split(r"[;,]", args.items)])
        print("Context set.")
        return 0
    if args.cmd == "clear":
        clear(); print("Cleared."); return 0
    if args.cmd == "resolve":
        res = resolve_now(" ".join(args.text))
        print(json.dumps({"item": res[0], "instruction": res[1]} if res else None, indent=2))
        return 0
    ctx = get_context()
    print(json.dumps(ctx, indent=2) if ctx else "No fresh follow-up context.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
