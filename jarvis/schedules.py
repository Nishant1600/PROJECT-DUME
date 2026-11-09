"""Scheduled routines (#54): "every weekday at nine, brief me".

Named instructions on a timer. A schedule stores an instruction (a phrase JARVIS would
otherwise hear — "brief me", a saved routine name, any request) plus a recurrence, and
the engine fires it when due. The instruction runs through the normal turn path, so
"brief me" expands to the morning brief, a routine name runs that routine, and anything
else just goes to the brain.

Recurrence understood (pure-Python, no cron library):
  * every day / daily at HH[:MM][am|pm]
  * every weekday / on weekdays at HH[:MM]
  * every weekend at HH[:MM]
  * every <weekday> at HH[:MM]        (e.g. "every monday at 9am")
  * every N hours|minutes  ·  hourly  ·  every hour

Firing is gentle: it waits until JARVIS is idle and holds everything during focus mode.

    python -m jarvis.schedules list
    python -m jarvis.schedules add "brief me" every weekday at 9am
    python -m jarvis.schedules remove <id>
    python -m jarvis.schedules due            # what would fire right now
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import re
import sys
import threading
import time
from typing import Callable

from .config import RUNTIME_DIR

log = logging.getLogger(__name__)

SCHEDULES_FILE = RUNTIME_DIR / "schedules.json"

_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
             "friday": 4, "saturday": 5, "sunday": 6,
             "mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3,
             "thurs": 3, "fri": 4, "sat": 5, "sun": 6}


# --------------------------------------------------------------------------- parsing

def _parse_time(low: str) -> tuple[int, int] | None:
    m = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", low)
    if not m:
        # bare "at nine" handled by word->number below; also allow "9am" without "at"
        m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", low)
        if not m:
            return None
    hh = int(m.group(1))
    mm = int(m.group(2) or 0)
    ap = m.group(3)
    if ap == "pm" and hh < 12:
        hh += 12
    if ap == "am" and hh == 12:
        hh = 0
    if hh > 23 or mm > 59:
        return None
    return hh, mm


_WORD_HOURS = {"midnight": 0, "noon": 12, "one": 1, "two": 2, "three": 3, "four": 4,
               "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
               "eleven": 11, "twelve": 12}


def _normalise_words(low: str) -> str:
    """Turn 'at nine' / 'at nine am' into 'at 9' so the time parser can read it."""
    def repl(m):
        return f"at {_WORD_HOURS[m.group(1)]}"
    return re.sub(r"\bat\s+(" + "|".join(_WORD_HOURS) + r")\b", repl, low)


def parse_schedule(text: str) -> tuple[str, dict] | None:
    """Return (clean_instruction, spec) or None if the text has no recurrence.

    spec is one of:
      {"freq": "daily|weekdays|weekends", "h": H, "m": M}
      {"freq": "weekly", "weekday": 0-6, "h": H, "m": M}
      {"freq": "interval", "minutes": N}
    """
    t = " ".join((text or "").split())
    low = _normalise_words(t.lower())

    spec: dict | None = None
    sched_span: tuple[int, int] | None = None

    # interval: "every N hours/minutes", "hourly", "every hour"
    m = re.search(r"\bevery\s+(\d+)\s*(hours?|hrs?|minutes?|mins?)\b", low)
    if m:
        n = int(m.group(1))
        minutes = n * 60 if m.group(2).startswith(("hour", "hr")) else n
        if minutes >= 1:
            spec = {"freq": "interval", "minutes": minutes}
            sched_span = m.span()
    if spec is None:
        m = re.search(r"\b(hourly|every hour)\b", low)
        if m:
            spec = {"freq": "interval", "minutes": 60}
            sched_span = m.span()
    if spec is None:
        m = re.search(r"\bevery\s+(\d+)\s*hourly\b", low)  # defensive; unlikely
        if m:
            spec = {"freq": "interval", "minutes": int(m.group(1)) * 60}
            sched_span = m.span()

    # day-based (need a time)
    if spec is None:
        hm = _parse_time(low)
        if re.search(r"\b(every\s+week\s?day|on\s+weekdays?|weekdays)\b", low):
            if hm:
                spec = {"freq": "weekdays", "h": hm[0], "m": hm[1]}
        elif re.search(r"\b(every\s+weekend|on\s+weekends?|weekends)\b", low):
            if hm:
                spec = {"freq": "weekends", "h": hm[0], "m": hm[1]}
        else:
            wm = re.search(r"\b(?:every|each|on)\s+(" + "|".join(_WEEKDAYS) + r")s?\b", low)
            if wm and hm:
                spec = {"freq": "weekly", "weekday": _WEEKDAYS[wm.group(1)], "h": hm[0], "m": hm[1]}
            elif re.search(r"\b(every\s*day|each\s*day|daily|every\s+morning|each\s+morning)\b", low) and hm:
                spec = {"freq": "daily", "h": hm[0], "m": hm[1]}

    if spec is None:
        return None

    # strip the scheduling words out of the original text to leave the instruction
    instruction = t
    for pat in (
        r"\bevery\s+\d+\s*(?:hours?|hrs?|minutes?|mins?)\b",
        r"\b(?:hourly|every hour)\b",
        r"\b(?:every\s+week\s?day|on\s+weekdays?|weekdays)\b",
        r"\b(?:every\s+weekend|on\s+weekends?|weekends)\b",
        r"\b(?:every|each|on)\s+(?:" + "|".join(_WEEKDAYS) + r")s?\b",
        r"\b(?:every\s*day|each\s*day|daily|every\s+morning|each\s+morning)\b",
        r"\bat\s+(?:\d{1,2}(?::\d{2})?\s*(?:am|pm)?|" + "|".join(_WORD_HOURS) + r")\b",
        r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
    ):
        instruction = re.sub(pat, " ", instruction, flags=re.I)
    instruction = re.sub(r"^(?:schedule|please|and)\b", " ", instruction.strip(), flags=re.I)
    instruction = re.sub(r"[,:]", " ", instruction)
    instruction = re.sub(r"\s+", " ", instruction).strip(" ,.-")
    return instruction, spec


def describe(spec: dict) -> str:
    if spec["freq"] == "interval":
        n = spec["minutes"]
        if n % 60 == 0:
            h = n // 60
            return "every hour" if h == 1 else f"every {h} hours"
        return "every minute" if n == 1 else f"every {n} minutes"
    tm = datetime.time(spec["h"], spec["m"]).strftime("%I:%M %p").lstrip("0")
    if spec["freq"] == "daily":
        return f"every day at {tm}"
    if spec["freq"] == "weekdays":
        return f"every weekday at {tm}"
    if spec["freq"] == "weekends":
        return f"every weekend at {tm}"
    if spec["freq"] == "weekly":
        day = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][spec["weekday"]]
        return f"every {day} at {tm}"
    return "on a schedule"


# --------------------------------------------------------------------------- next-fire

def next_fire(spec: dict, after: datetime.datetime) -> datetime.datetime:
    """The next time this spec fires strictly after `after`."""
    if spec["freq"] == "interval":
        return after + datetime.timedelta(minutes=int(spec["minutes"]))
    cand = after.replace(hour=int(spec["h"]), minute=int(spec["m"]), second=0, microsecond=0)
    if cand <= after:
        cand += datetime.timedelta(days=1)
    for _ in range(8):
        wd = cand.weekday()
        freq = spec["freq"]
        if freq == "daily":
            return cand
        if freq == "weekdays" and wd < 5:
            return cand
        if freq == "weekends" and wd >= 5:
            return cand
        if freq == "weekly" and wd == int(spec["weekday"]):
            return cand
        cand += datetime.timedelta(days=1)
    return cand


# --------------------------------------------------------------------------- store

class Schedules:
    def _load(self) -> list[dict]:
        try:
            return json.loads(SCHEDULES_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _save(self, items: list[dict]) -> None:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        SCHEDULES_FILE.write_text(json.dumps(items, indent=2), encoding="utf-8")

    def add(self, instruction: str, spec: dict) -> dict:
        items = self._load()
        entry = {"id": _short_id(items), "instruction": instruction.strip(),
                 "spec": spec, "created": time.time(), "last_run": None}
        items.append(entry)
        self._save(items)
        return entry

    def remove(self, ident: str) -> bool:
        items = self._load()
        kept = [it for it in items if it["id"] != ident
                and it["instruction"].lower() != ident.strip().lower()]
        if len(kept) == len(items):
            return False
        self._save(kept)
        return True

    def all(self) -> list[dict]:
        return self._load()

    def mark_run(self, ident: str, now_ts: float | None = None) -> None:
        now_ts = now_ts if now_ts is not None else time.time()
        items = self._load()
        for it in items:
            if it["id"] == ident:
                it["last_run"] = now_ts
        self._save(items)

    def due(self, now: datetime.datetime | None = None) -> list[dict]:
        """Schedules whose next fire time has arrived (and not yet run for that slot)."""
        now = now or datetime.datetime.now()
        out = []
        for it in self._load():
            base_ts = it.get("last_run") or it.get("created") or now.timestamp()
            base = datetime.datetime.fromtimestamp(base_ts)
            if next_fire(it["spec"], base) <= now:
                out.append(it)
        return out


def _short_id(items: list[dict]) -> str:
    used = {it["id"] for it in items}
    n = 1
    while f"s{n}" in used:
        n += 1
    return f"s{n}"


# --------------------------------------------------------------------------- command patterns

SAVE_RE = re.compile(r"^(?:/schedule\s+|schedule\s+)(?P<body>.+)$", re.I)
LIST_RE = re.compile(r"^(?:list|show|what are)\s+(?:my\s+)?schedules\W*$", re.I)
DELETE_RE = re.compile(
    r"^(?:/unschedule\s+|(?:remove|delete|drop|cancel|stop)\s+(?:the\s+)?schedule\b[:,]?\s*)(?P<ident>.+)$", re.I)
_KEYWORD_RE = re.compile(r"\b(every|each|daily|hourly|weekday|weekend|"
                         + "|".join(_WEEKDAYS) + r")\b", re.I)


def has_schedule_keyword(text: str) -> bool:
    return bool(_KEYWORD_RE.search(text or ""))


# --------------------------------------------------------------------------- engine

class ScheduleEngine:
    """Background thread that fires due schedules through a run callback."""

    def __init__(self, bus, run: Callable[[str], None], is_idle: Callable[[], bool]):
        self.bus = bus
        self.run = run
        self.is_idle = is_idle
        self.store = Schedules()
        self._stop = threading.Event()

    def start(self) -> "ScheduleEngine":
        threading.Thread(target=self._loop, name="jarvis-schedules", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(30):
            try:
                from . import focus
                if focus.active():
                    continue
                if not self.is_idle():
                    continue
                for it in self.store.due():
                    self.store.mark_run(it["id"])
                    try:
                        from . import bus as B
                        self.bus.publish(B.NOTICE, text=f"Scheduled: {it['instruction']}", level="info")
                    except Exception:
                        pass
                    self.run(it["instruction"])
                    time.sleep(1)
            except Exception:
                log.exception("schedule loop error")


# --------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jarvis.schedules", description="Scheduled routines")
    sub = ap.add_subparsers(dest="cmd")

    a = sub.add_parser("add", help="add a schedule")
    a.add_argument("rest", nargs="+", help='e.g. "brief me" every weekday at 9am')
    r = sub.add_parser("remove", help="remove by id or instruction")
    r.add_argument("ident")
    sub.add_parser("list", help="list schedules")
    sub.add_parser("due", help="what would fire right now")

    args = ap.parse_args(argv)
    store = Schedules()

    if args.cmd == "add":
        parsed = parse_schedule(" ".join(args.rest))
        if not parsed:
            print("I couldn't find a recurrence. Try: \"brief me\" every weekday at 9am")
            return 1
        instr, spec = parsed
        if not instr:
            print("What should I run on that schedule?")
            return 1
        e = store.add(instr, spec)
        print(f"Scheduled: {e['instruction']} — {describe(spec)} (id {e['id']}).")
        return 0
    if args.cmd == "remove":
        print("Removed." if store.remove(args.ident) else f"No schedule matching {args.ident!r}.")
        return 0
    if args.cmd == "due":
        items = store.due()
        print("\n".join(f"  [{it['id']}] {it['instruction']}" for it in items) or "Nothing due now.")
        return 0

    items = store.all()
    if not items:
        print("No schedules yet.")
        return 0
    for it in items:
        print(f"  [{it['id']}] {it['instruction']} — {describe(it['spec'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
