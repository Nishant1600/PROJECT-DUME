"""Personal study knowledge base (#55/#56 backing store).

When the study tutor explains a topic it saves the notes here, as one Markdown file
per topic under memory/study/. Revision mode quizzes you from these same notes. Pure
file I/O, no network, no model call — so it's fast and works offline.

    python -m jarvis.studykb list
    python -m jarvis.studykb show "<topic>"
    echo "<notes>" | python -m jarvis.studykb save "<topic>"
"""
from __future__ import annotations

import argparse
import datetime
import re
import sys

from .config import MEMORY_DIR

STUDY_DIR = MEMORY_DIR / "study"


def _slug(topic: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (topic or "").lower()).strip("-")
    return s[:60] or "topic"


def _path(slug: str):
    return STUDY_DIR / f"{slug}.md"


def save(topic: str, notes: str) -> dict:
    """Write (or overwrite) the notes for a topic. Returns {slug, topic, path}."""
    STUDY_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slug(topic)
    today = datetime.date.today().isoformat()
    notes_clean = notes.strip()
    # don't add our own H1 if the notes already open with a Markdown heading (avoids a doubled title)
    heading = "" if re.match(r"^#{1,6}\s", notes_clean) else f"# {topic.strip()}\n\n"
    body = f"<!-- topic: {topic.strip()} | saved: {today} -->\n{heading}{notes_clean}\n"
    _path(slug).write_text(body, encoding="utf-8")
    return {"slug": slug, "topic": topic.strip(), "path": str(_path(slug))}


def _read(slug: str) -> str:
    try:
        return _path(slug).read_text(encoding="utf-8")
    except OSError:
        return ""


def _topic_of(text: str, slug: str) -> str:
    m = re.search(r"topic:\s*(.+?)\s*\|", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?m)^#\s+(.+)$", text)
    return m.group(1).strip() if m else slug.replace("-", " ")


def topics() -> list[dict]:
    if not STUDY_DIR.exists():
        return []
    out = []
    for p in sorted(STUDY_DIR.glob("*.md")):
        text = p.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"saved:\s*(\d{4}-\d{2}-\d{2})", text)
        out.append({"slug": p.stem, "topic": _topic_of(text, p.stem),
                    "saved": m.group(1) if m else None, "chars": len(text)})
    return out


def get(slug_or_topic: str) -> str | None:
    """Return the notes body (sans the metadata comment) for a topic, by slug or name."""
    slug = _slug(slug_or_topic)
    text = _read(slug)
    if not text and STUDY_DIR.exists():
        # try matching by topic name
        for t in topics():
            if t["topic"].lower() == slug_or_topic.strip().lower():
                text = _read(t["slug"])
                break
    if not text:
        return None
    return re.sub(r"<!--.*?-->\n?", "", text, flags=re.S).strip()


def search(query: str) -> list[dict]:
    q = (query or "").lower().split()
    if not q:
        return topics()
    out = []
    for p in (STUDY_DIR.glob("*.md") if STUDY_DIR.exists() else []):
        text = p.read_text(encoding="utf-8", errors="ignore").lower()
        if all(w in text for w in q):
            out.append({"slug": p.stem, "topic": _topic_of(text, p.stem)})
    return out


def remove(slug_or_topic: str) -> bool:
    slug = _slug(slug_or_topic)
    if _path(slug).exists():
        _path(slug).unlink()
        return True
    for t in topics():
        if t["topic"].lower() == slug_or_topic.strip().lower():
            _path(t["slug"]).unlink(missing_ok=True)
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jarvis.studykb", description="Personal study knowledge base")
    sub = ap.add_subparsers(dest="cmd")
    sv = sub.add_parser("save", help="save notes for a topic (notes on stdin)")
    sv.add_argument("topic")
    sh = sub.add_parser("show", help="print a topic's notes")
    sh.add_argument("topic")
    sub.add_parser("list", help="list saved topics")
    rm = sub.add_parser("remove", help="delete a topic")
    rm.add_argument("topic")

    args = ap.parse_args(argv)
    if args.cmd == "save":
        notes = sys.stdin.read()
        if not notes.strip():
            print("No notes on stdin to save.")
            return 1
        info = save(args.topic, notes)
        print(f"Saved study notes: {info['topic']} ({info['slug']}).")
        return 0
    if args.cmd == "show":
        body = get(args.topic)
        print(body or f"No saved notes for {args.topic!r}.")
        return 0 if body else 1
    if args.cmd == "remove":
        print("Removed." if remove(args.topic) else f"No topic {args.topic!r}.")
        return 0
    items = topics()
    if not items:
        print("No study notes yet. Say \"teach me <topic>\" to start your knowledge base.")
        return 0
    for t in items:
        print(f"  {t['slug']:30} {t['topic']}  (saved {t['saved']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
