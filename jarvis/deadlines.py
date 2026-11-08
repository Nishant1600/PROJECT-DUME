"""Deadline tracker for JARVIS (README roadmap: "a deadline tracker").

A dependency-light store of dated deadlines plus a natural-date parser, so JARVIS can
answer "what's due this week?" and warn you as a date approaches. Two ways things land here:

  * You tell it: "add deadline submit the DBMS assignment by Friday" (handled locally,
    no model call) -> parsed and stored in runtime/deadlines.json.
  * The brain finds them: when it reads mail / calendar / Notion it can register due
    dates it discovers with `python -m jarvis.deadlines add "<title>" <date> --source mail`.

Reporting is urgency-ranked. The nudge engine reads `due(...)` to warn you ahead of time
(opt-in, like every unsolicited nudge). Nothing here sends or changes anything outside the
local store - it only tracks and reports.

    python -m jarvis.deadlines add "submit assignment" friday
    python -m jarvis.deadlines list
    python -m jarvis.deadlines due --days 7
    python -m jarvis.deadlines remove <id>
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys

from .config import RUNTIME_DIR

DEADLINES_FILE = RUNTIME_DIR / "deadlines.json"

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
             "friday": 4, "saturday": 5, "sunday": 6,
             "mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3,
             "thurs": 3, "fri": 4, "sat": 5, "sun": 6}


# --------------------------------------------------------------------------- date parsing

def parse_due(text: str, now: datetime.date | None = None) -> tuple[str, datetime.date] | None:
    """Pull a due date out of free text. Returns (clean_title, date) or None.

    Understands: today / tomorrow / tonight, "in N days|weeks", weekday names (optionally
    "next <weekday>"), ISO YYYY-MM-DD, DD/MM[/YYYY], "25 nov[ember] [2026]", "nov 25",
    and "end of (the) month". Pure-Python, no third-party date library.
    """
    now = now or datetime.date.today()
    t = " ".join((text or "").split())
    low = t.lower()

    def strip(pattern: str) -> str:
        cleaned = re.sub(pattern, "", t, flags=re.I)
        cleaned = re.sub(r"\b(by|on|due|before|until|till)\b\s*$", "", cleaned.strip(), flags=re.I)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")
        return cleaned

    # today / tomorrow / tonight
    if re.search(r"\b(today|tonight)\b", low):
        return strip(r"\b(by|on|due|before)?\s*(today|tonight)\b"), now
    if re.search(r"\btomorrow\b", low):
        return strip(r"\b(by|on|due|before)?\s*tomorrow\b"), now + datetime.timedelta(days=1)

    # in N days / weeks
    m = re.search(r"\bin\s+(\d+)\s*(day|week)s?\b", low)
    if m:
        n = int(m.group(1)) * (7 if m.group(2) == "week" else 1)
        return strip(r"\b(by|on|due|before)?\s*in\s+\d+\s*(?:day|week)s?\b"), now + datetime.timedelta(days=n)

    # ISO YYYY-MM-DD
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", low)
    if m:
        try:
            d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return strip(r"\b\d{4}-\d{1,2}-\d{1,2}\b"), d
        except ValueError:
            return None

    # DD/MM[/YYYY] or DD-MM[-YYYY]  (day-first, matching the user's locale)
    m = re.search(r"\b(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?\b", low)
    if m:
        day, mon = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else now.year
        if year < 100:
            year += 2000
        d = _safe_date(year, mon, day, now)
        if d:
            return strip(r"\b\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?\b"), d

    # "25 nov[ember] [2026]"  or  "nov[ember] 25[, 2026]"
    m = re.search(r"\b(\d{1,2})\s+([a-z]{3,9})\.?(?:\s+(\d{4}))?\b", low)
    if m and m.group(2)[:3] in _MONTHS:
        day, mon = int(m.group(1)), _MONTHS[m.group(2)[:3]]
        year = int(m.group(3)) if m.group(3) else now.year
        d = _safe_date(year, mon, day, now)
        if d:
            return strip(r"\b\d{1,2}\s+[a-z]{3,9}\.?(?:\s+\d{4})?\b"), d
    m = re.search(r"\b([a-z]{3,9})\.?\s+(\d{1,2})(?:,?\s+(\d{4}))?\b", low)
    if m and m.group(1)[:3] in _MONTHS:
        mon, day = _MONTHS[m.group(1)[:3]], int(m.group(2))
        year = int(m.group(3)) if m.group(3) else now.year
        d = _safe_date(year, mon, day, now)
        if d:
            return strip(r"\b[a-z]{3,9}\.?\s+\d{1,2}(?:,?\s+\d{4})?\b"), d

    # end of month
    if re.search(r"\bend of (?:the )?month\b|\beom\b", low):
        last = _last_day_of_month(now.year, now.month)
        return strip(r"\b(by|on|due|before)?\s*(end of (?:the )?month|eom)\b"), last

    # weekday, optionally "next friday"
    m = re.search(r"\b(next\s+)?(" + "|".join(_WEEKDAYS) + r")\b", low)
    if m:
        target = _WEEKDAYS[m.group(2)]
        ahead = (target - now.weekday()) % 7
        if ahead == 0 or m.group(1):          # "friday" today -> next friday; "next" always jumps
            ahead += 7 if (ahead == 0 or m.group(1)) else 0
            if ahead == 0:
                ahead = 7
        d = now + datetime.timedelta(days=ahead)
        return strip(r"\b(by|on|due|before)?\s*(next\s+)?(?:" + "|".join(_WEEKDAYS) + r")\b"), d

    return None


def _safe_date(year: int, month: int, day: int, now: datetime.date) -> datetime.date | None:
    try:
        d = datetime.date(year, month, day)
    except ValueError:
        return None
    # a bare month/day already in the past this year almost always means next year
    if d < now and (now - d).days > 1 and year == now.year:
        try:
            d = datetime.date(year + 1, month, day)
        except ValueError:
            pass
    return d


def _last_day_of_month(year: int, month: int) -> datetime.date:
    if month == 12:
        return datetime.date(year, 12, 31)
    return datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)


# --------------------------------------------------------------------------- store

class Deadlines:
    def _load(self) -> list[dict]:
        try:
            return json.loads(DEADLINES_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _save(self, items: list[dict]) -> None:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        DEADLINES_FILE.write_text(json.dumps(items, indent=2), encoding="utf-8")

    def add(self, title: str, due: datetime.date, source: str = "you") -> dict:
        items = self._load()
        iso = due.isoformat()
        # dedup: same title + same date already tracked
        for it in items:
            if it["title"].lower() == title.lower() and it["due"] == iso:
                return it
        entry = {"id": _short_id(items), "title": title.strip(), "due": iso,
                 "source": source, "added": datetime.date.today().isoformat()}
        items.append(entry)
        self._save(items)
        return entry

    def remove(self, ident: str) -> bool:
        items = self._load()
        kept = [it for it in items if it["id"] != ident
                and it["title"].lower() != ident.lower()]
        if len(kept) == len(items):
            return False
        self._save(kept)
        return True

    def all(self) -> list[dict]:
        return sorted(self._load(), key=lambda it: it["due"])

    def purge_past(self, keep_days: int = 1) -> None:
        """Drop deadlines more than keep_days in the past, so the store doesn't grow forever."""
        cutoff = datetime.date.today() - datetime.timedelta(days=keep_days)
        items = [it for it in self._load() if _as_date(it["due"]) >= cutoff]
        self._save(items)

    def due(self, within_days: int = 7, now: datetime.date | None = None) -> list[dict]:
        """Upcoming (and overdue) deadlines inside the window, each annotated with urgency."""
        now = now or datetime.date.today()
        out = []
        for it in self.all():
            d = _as_date(it["due"])
            days = (d - now).days
            if days <= within_days:
                out.append({**it, "days_left": days, "urgency": _urgency(days),
                            "when": _human_when(days, d)})
        return out


def _urgency(days: int) -> str:
    if days < 0:
        return "overdue"
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days <= 3:
        return "soon"
    return "upcoming"


def _human_when(days: int, d: datetime.date) -> str:
    if days < 0:
        return f"overdue by {-days} day{'s' if -days != 1 else ''}"
    if days == 0:
        return "due today"
    if days == 1:
        return "due tomorrow"
    if days <= 6:
        return f"due {d.strftime('%A')}"           # "due Friday"
    return f"due {d.strftime('%a %d %b')}"          # "due Tue 25 Nov"


def _as_date(iso: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(iso)
    except ValueError:
        return datetime.date.max


def _short_id(items: list[dict]) -> str:
    used = {it["id"] for it in items}
    n = 1
    while f"d{n}" in used:
        n += 1
    return f"d{n}"


# --------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jarvis.deadlines", description="Track dated deadlines")
    sub = ap.add_subparsers(dest="cmd")

    a = sub.add_parser("add", help="add a deadline")
    a.add_argument("title", help="what is due")
    a.add_argument("when", nargs="+", help="when it's due (friday, 2026-11-25, 'in 3 days'...)")
    a.add_argument("--source", default="you")

    d = sub.add_parser("due", help="list upcoming/overdue deadlines")
    d.add_argument("--days", type=int, default=7)
    d.add_argument("--json", action="store_true")

    sub.add_parser("list", help="list every tracked deadline")
    r = sub.add_parser("remove", help="remove a deadline by id or title")
    r.add_argument("ident")

    args = ap.parse_args(argv)
    store = Deadlines()
    store.purge_past()

    if args.cmd == "add":
        parsed = parse_due(" ".join(args.when))
        if not parsed:
            # maybe the date got merged into the title: try the whole thing
            parsed = parse_due(f"{args.title} {' '.join(args.when)}")
            if not parsed:
                print("I couldn't understand that date. Try 'friday', '2026-11-25', or 'in 3 days'.")
                return 1
            title, due = parsed
            title = title or args.title
        else:
            _, due = parsed
            title = args.title
        entry = store.add(title, due, source=args.source)
        print(f"Tracking: {entry['title']} — due {due.strftime('%a %d %b %Y')} (id {entry['id']}).")
        return 0

    if args.cmd == "remove":
        print("Removed." if store.remove(args.ident) else f"No deadline matching {args.ident!r}.")
        return 0

    if args.cmd == "list":
        items = store.all()
        if not items:
            print("No deadlines tracked.")
            return 0
        for it in items:
            print(f"  [{it['id']}] {it['title']} — {it['due']} (from {it['source']})")
        return 0

    # default: due
    days = getattr(args, "days", 7)
    items = store.due(days)
    if getattr(args, "json", False):
        print(json.dumps(items, indent=2))
        return 0
    if not items:
        print(f"Nothing due in the next {days} days.")
        return 0
    print(f"Due in the next {days} days:\n")
    for it in items:
        tag = it["urgency"].upper()
        print(f"  [{tag}] {it['title']} — {it['when']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
