"""Revision mode (#56): spaced-repetition quizzing from your own study notes.

Quizzes you from the study knowledge base (jarvis.studykb) and schedules each topic's
next review with an SM-2-style algorithm, so topics you find hard come back sooner and
ones you know well fade into the distance. The scheduling state lives in
runtime/revision.json; the notes themselves live in the study KB.

    python -m jarvis.revision due                 # topics due for review
    python -m jarvis.revision record <slug> <0-5> # grade a review (0 = blank, 5 = perfect)
    python -m jarvis.revision list
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys

from .config import RUNTIME_DIR

REVISION_FILE = RUNTIME_DIR / "revision.json"

_TRIGGER = re.compile(
    r"^(?:/revise|/quiz|quiz me(?:\s+on\s+(?P<topic>.+))?|test me(?:\s+on\s+(?P<t2>.+))?|"
    r"revise(?:\s+(?P<t3>.+))?|revision(?:\s+mode)?|practice(?:\s+questions)?|"
    r"let'?s revise|time to revise)\W*$", re.I)


def is_trigger(text: str) -> bool:
    return bool(_TRIGGER.match((text or "").strip()))


def topic_of(text: str) -> str:
    m = _TRIGGER.match((text or "").strip())
    if not m:
        return ""
    for g in ("topic", "t2", "t3"):
        if m.group(g):
            return m.group(g).strip(" .?!")
    return ""


# --------------------------------------------------------------------------- SM-2

def schedule_review(state: dict, grade: int, today: datetime.date | None = None) -> dict:
    """Return the updated scheduling state after a review graded 0-5 (SM-2-lite)."""
    today = today or datetime.date.today()
    grade = max(0, min(5, int(grade)))
    reps = int(state.get("reps", 0))
    ease = float(state.get("ease", 2.5))
    interval = int(state.get("interval", 0))

    if grade < 3:                      # failed recall -> relearn tomorrow
        reps = 0
        interval = 1
    else:
        reps += 1
        if reps == 1:
            interval = 1
        elif reps == 2:
            interval = 6
        else:
            interval = max(1, round(interval * ease))
    ease = max(1.3, ease + (0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02)))
    due = today + datetime.timedelta(days=interval)
    return {"reps": reps, "ease": round(ease, 3), "interval": interval,
            "due": due.isoformat(), "last": today.isoformat()}


# --------------------------------------------------------------------------- store

class Revision:
    def _load(self) -> dict:
        try:
            return json.loads(REVISION_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        REVISION_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def state(self, slug: str) -> dict:
        return self._load().get(slug, {})

    def record(self, slug: str, grade: int, today: datetime.date | None = None) -> dict:
        data = self._load()
        data[slug] = schedule_review(data.get(slug, {}), grade, today)
        self._save(data)
        return data[slug]

    def due(self, now: datetime.date | None = None) -> list[str]:
        """Study-KB slugs due for review: never-reviewed topics, then anything past its due date."""
        now = now or datetime.date.today()
        from . import studykb
        data = self._load()
        due = []
        for t in studykb.topics():
            slug = t["slug"]
            st = data.get(slug)
            if not st:
                due.append(slug)                       # new topic -> review it
            else:
                try:
                    if datetime.date.fromisoformat(st["due"]) <= now:
                        due.append(slug)
                except (KeyError, ValueError):
                    due.append(slug)
        return due

    def all(self) -> dict:
        return self._load()


def revision_prompt(cfg: dict | None = None, topic: str = "") -> str:
    """Instruction the brain runs to quiz the user from their saved notes."""
    from . import studykb
    if topic:
        target = topic
        slug_hint = studykb._slug(topic)
    else:
        due = Revision().due()
        if not due:
            all_topics = [t["slug"] for t in studykb.topics()]
            due = all_topics[:1]
        slug_hint = due[0] if due else ""
        target = slug_hint.replace("-", " ") if slug_hint else ""
    if not target:
        return ("I don't have any saved study notes to quiz you from yet. Tell the user to say "
                "\"teach me <topic>\" first, then revision mode can quiz them. Say this briefly.")
    return (
        f"Run a revision session on: {target}.\n"
        f"First read my saved notes: run `python -m jarvis.studykb show \"{slug_hint}\"` via Bash. "
        "Then quiz me out loud: ask one question at a time, wait for my spoken answer, tell me if "
        "I'm right and nudge me if not. Ask three or four questions. At the end, judge how I did "
        "overall on a 0-5 scale (0 = blank, 3 = shaky, 5 = perfect) and record it so I'm re-tested "
        f"at the right time: run `python -m jarvis.revision record {slug_hint} <grade>`. "
        "Keep it encouraging and brief."
    )


# --------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jarvis.revision", description="Spaced-repetition revision")
    sub = ap.add_subparsers(dest="cmd")
    rec = sub.add_parser("record", help="grade a review (0-5)")
    rec.add_argument("slug")
    rec.add_argument("grade", type=int)
    sub.add_parser("due", help="topics due for review")
    sub.add_parser("list", help="all review schedules")

    args = ap.parse_args(argv)
    rev = Revision()
    if args.cmd == "record":
        st = rev.record(args.slug, args.grade)
        print(f"Recorded. Next review for {args.slug} in {st['interval']} day(s) (due {st['due']}).")
        return 0
    if args.cmd == "due":
        due = rev.due()
        print("\n".join(f"  {s}" for s in due) if due else "Nothing due — you're all caught up.")
        return 0
    data = rev.all()
    if not data:
        print("No reviews recorded yet.")
        return 0
    for slug, st in sorted(data.items()):
        print(f"  {slug:30} due {st.get('due')}  (interval {st.get('interval')}d, ease {st.get('ease')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
