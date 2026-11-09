"""Agenda / deadline board (#58): a glanceable view of what's on.

Assembles one compact "board" from the things JARVIS already tracks locally — upcoming
deadlines, scheduled routines and reminders — with no network and no model call. It's
exposed three ways: spoken ("what's on my board?"), as a text board on the terminal, and
as a JSON payload the HUD can render into a panel (publish_board).

    python -m jarvis.agenda            # text board
    python -m jarvis.agenda --json     # the payload the HUD consumes
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys

_TRIGGER = re.compile(
    r"^(?:/agenda|/board|(?:what(?:'s| is)?\s+(?:on\s+)?)?(?:my|the)\s+(?:agenda|board)|"
    r"agenda|the board|show (?:me )?(?:my )?(?:agenda|board))\W*$", re.I)


def is_trigger(text: str) -> bool:
    return bool(_TRIGGER.match((text or "").strip()))


def board(cfg: dict | None = None, now: datetime.datetime | None = None) -> dict:
    """Assemble the board payload: date, deadlines, schedules, reminders."""
    now = now or datetime.datetime.now()
    today = now.date()

    deadlines = []
    try:
        from .deadlines import Deadlines
        for it in Deadlines().due(within_days=7, now=today):
            deadlines.append({"title": it["title"], "when": it["when"], "urgency": it["urgency"]})
    except Exception:
        pass

    schedules = []
    try:
        from .schedules import Schedules, next_fire, describe
        for it in Schedules().all():
            base = datetime.datetime.fromtimestamp(it.get("last_run") or it.get("created") or now.timestamp())
            nf = next_fire(it["spec"], max(base, now - datetime.timedelta(seconds=1)))
            schedules.append({"instruction": it["instruction"], "recurrence": describe(it["spec"]),
                              "next": nf.strftime("%a %H:%M")})
    except Exception:
        pass

    reminders = []
    try:
        from .nudges import Reminders
        for r in Reminders().all()[:5]:
            when = datetime.datetime.fromtimestamp(r["ts"]).strftime("%a %H:%M")
            reminders.append({"text": r["text"], "when": when})
    except Exception:
        pass

    who = (cfg or {}).get("address_as") or "Sir"
    return {"date": today.isoformat(), "weekday": today.strftime("%A"),
            "address_as": who, "deadlines": deadlines,
            "schedules": schedules, "reminders": reminders}


def text_board(b: dict) -> str:
    out = [f"— {b['weekday']}, {b['date']} —", ""]
    if b["deadlines"]:
        out.append("Deadlines:")
        out += [f"  [{d['urgency'].upper()}] {d['title']} ({d['when']})" for d in b["deadlines"]]
    if b["reminders"]:
        out.append("Reminders:")
        out += [f"  {r['when']}  {r['text']}" for r in b["reminders"]]
    if b["schedules"]:
        out.append("Scheduled:")
        out += [f"  {s['instruction']} — {s['recurrence']} (next {s['next']})" for s in b["schedules"]]
    if len(out) <= 2:
        out.append("Nothing tracked right now.")
    return "\n".join(out)


def spoken(b: dict) -> str:
    who = b.get("address_as", "Sir").lower()
    bits = []
    urgent = [d for d in b["deadlines"] if d["urgency"] in ("overdue", "today", "tomorrow")]
    if urgent:
        bits.append(f"{len(urgent)} deadline{'s' if len(urgent) != 1 else ''} needing attention — "
                    + ", ".join(f"{d['title']} {d['when']}" for d in urgent[:2]))
    elif b["deadlines"]:
        bits.append(f"{len(b['deadlines'])} deadline{'s' if len(b['deadlines']) != 1 else ''} this week")
    if b["reminders"]:
        bits.append(f"{len(b['reminders'])} reminder{'s' if len(b['reminders']) != 1 else ''} set")
    if not bits:
        return f"Your board is clear, {who}."
    return f"On your board, {who}: " + "; ".join(bits) + "."


def publish_board(bus, cfg: dict | None = None) -> dict:
    """Publish the board on the event bus for the HUD panel. Best-effort."""
    b = board(cfg)
    try:
        from . import bus as B
        topic = getattr(B, "BOARD", "board")
        bus.publish(topic, **b)
    except Exception:
        pass
    return b


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jarvis.agenda", description="Glanceable agenda board")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    cfg = {}
    try:
        from .config import load_config
        cfg = load_config()
    except Exception:
        pass
    b = board(cfg)
    print(json.dumps(b, indent=2) if args.json else text_board(b))
    return 0


if __name__ == "__main__":
    sys.exit(main())
