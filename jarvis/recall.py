"""Universal recall for JARVIS (feature #40).

Searches everything JARVIS remembers about the user - the memory files and the
running conversation transcript - and returns the best-matching snippets, so the
brain can answer "what did I say about X last week?" without re-reading everything.

    python -m jarvis.recall "telegram internship bot"
    python -m jarvis.recall "design preferences" --k 8

Read-only. Keeps output small (token-light): only the top matches, each trimmed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .config import MEMORY_DIR, RUNTIME_DIR

SOURCES: list[tuple[Path, str]] = [
    (MEMORY_DIR / "summary.md", "recent summary"),
    (MEMORY_DIR / "facts.md", "a fact you told me"),
    (MEMORY_DIR / "profile.md", "how you work"),
    (MEMORY_DIR / "design.md", "how you build sites"),
    (MEMORY_DIR / "rules.md", "your rules"),
    (MEMORY_DIR / "identity.md", "identity"),
    (MEMORY_DIR / "topics.md", "topics you follow"),
    (MEMORY_DIR / "references.md", "a source I read for you"),
]

_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
         "was", "were", "my", "me", "i", "about", "what", "did", "say", "do", "you",
         "that", "this", "it", "with", "how", "when", "where", "which", "last", "week"}


def _terms(query: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) >= 2 and w not in _STOP]


def _score(text: str, terms: list[str]) -> int:
    low = text.lower()
    distinct = sum(1 for t in terms if t in low)
    occurrences = sum(low.count(t) for t in terms)
    # distinct-term coverage dominates; occurrences break ties
    return distinct * 100 + occurrences


def _units() -> list[tuple[str, str]]:
    """(source_label, text) for every searchable unit: md lines + transcript turns."""
    units: list[tuple[str, str]] = []
    for path, label in SOURCES:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
        for line in text.splitlines():
            line = line.strip().lstrip("#-*> ").strip()
            if len(line) >= 4:
                units.append((label, line))
    tp = RUNTIME_DIR / "transcript.jsonl"
    if tp.exists():
        for raw in tp.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                item = json.loads(raw)
            except ValueError:
                continue
            who = "you said" if item.get("role") == "user" else "I said"
            txt = (item.get("text") or "").strip()
            if len(txt) >= 4:
                units.append((who, txt))
    return units


def search(query: str, k: int = 6) -> list[dict]:
    terms = _terms(query)
    if not terms:
        return []
    scored = []
    for label, text in _units():
        s = _score(text, terms)
        if s >= 100:   # at least one query term present
            snippet = text if len(text) <= 240 else text[:237] + "..."
            scored.append({"score": s, "source": label, "text": snippet})
    scored.sort(key=lambda d: d["score"], reverse=True)
    # de-duplicate identical snippets, keep the best-scoring
    seen, out = set(), []
    for d in scored:
        key = d["text"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
        if len(out) >= k:
            break
    return out


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jarvis.recall", description="Search what JARVIS remembers")
    ap.add_argument("query", help="what to look for")
    ap.add_argument("--k", type=int, default=6, help="max results (default 6)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    hits = search(args.query, args.k)
    if args.json:
        print(json.dumps(hits, indent=2))
        return 0
    if not hits:
        print(f"Nothing in memory matches {args.query!r}.")
        return 1
    print(f"Found {len(hits)} match(es) for {args.query!r}:\n")
    for h in hits:
        print(f"  ({h['source']}) {h['text']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
