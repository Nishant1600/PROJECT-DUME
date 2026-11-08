"""Research references for JARVIS (feature #32).

The brain does the researching with its web-search/fetch tools; this just keeps the
sources it used, so "what did I read about X?" has citations to point back to.

    python -m jarvis.research add https://threejs.org/ --title "three.js docs" --note "r180 release notes"
    python -m jarvis.research list

References are saved to memory/references.md (one Markdown link per line) and are
included in `jarvis.recall`, so they surface when the user asks about a topic later.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from .config import MEMORY_DIR

REFS_FILE = MEMORY_DIR / "references.md"
_HEADER = "# Sources I've read for you\n"


def _read() -> str:
    try:
        return REFS_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""


def add(url: str, title: str | None = None, note: str | None = None) -> dict:
    url = url.strip()
    if not url:
        return {"ok": False, "error": "A URL is required."}
    existing = _read()
    if url in existing:
        return {"ok": False, "error": "Already saved that URL.", "url": url}
    title = (title or url).strip()
    stamp = datetime.now().strftime("%Y-%m-%d")
    line = f"- [{title}]({url})" + (f" - {note.strip()}" if note else "") + f" ({stamp})"
    body = existing if existing.strip() else _HEADER
    body = body.rstrip() + "\n" + line + "\n"
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    REFS_FILE.write_text(body, encoding="utf-8")
    return {"ok": True, "saved": line}


def list_refs() -> list[str]:
    return [ln.strip() for ln in _read().splitlines() if ln.strip().startswith("- ")]


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jarvis.research", description="Save/list research sources")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ap_add = sub.add_parser("add", help="save a source")
    ap_add.add_argument("url")
    ap_add.add_argument("--title", default=None)
    ap_add.add_argument("--note", default=None)
    sub.add_parser("list", help="list saved sources")
    args = ap.parse_args(argv)

    if args.cmd == "add":
        res = add(args.url, args.title, args.note)
        if not res.get("ok"):
            print(res["error"], file=sys.stderr)
            return 1
        print("Saved: " + res["saved"])
        return 0
    if args.cmd == "list":
        refs = list_refs()
        if not refs:
            print("No sources saved yet.")
            return 0
        print(f"{len(refs)} saved source(s):")
        for r in refs:
            print("  " + r)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
