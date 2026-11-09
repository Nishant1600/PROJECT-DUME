"""Your component library (#66): save and reuse your own UI blocks.

Save a snippet once ("save this as my hero section") and reuse it by name ("build it
with my hero section"). Snippets live as Markdown-wrapped HTML under memory/components/,
so they travel with your memory and work offline. Using one expands into an instruction
that hands the brain your exact saved code to adapt into what you're building.

    echo "<section>...</section>" | python -m jarvis.components save "hero section" --tags landing,hero
    python -m jarvis.components list
    python -m jarvis.components show "hero section"
"""
from __future__ import annotations

import argparse
import re
import sys

from .config import MEMORY_DIR

COMPONENTS_DIR = MEMORY_DIR / "components"

# "use my hero section" / "build it with my hero" / "with my nav component"
_USE = re.compile(
    r"\b(?:use|using|include|drop in|reuse|build (?:it|this)(?:\s+with)?|with)\s+my\s+"
    r"(?P<name>[a-z0-9 ._-]+?)\s*$", re.I)
_SAVE = re.compile(
    r"\bsave\s+(?:this|it|that)\s+as\s+my\s+(?P<name>[a-z0-9 ._-]+?)\s*$", re.I)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s[:50] or "component"


def _path(slug: str):
    return COMPONENTS_DIR / f"{slug}.md"


def save(name: str, code: str, tags: str = "") -> dict:
    COMPONENTS_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slug(name)
    taglist = ", ".join(t.strip() for t in re.split(r"[,\s]+", tags) if t.strip())
    body = (f"<!-- component: {name.strip()} | tags: {taglist} -->\n"
            f"# {name.strip()}\n\n```html\n{code.strip()}\n```\n")
    _path(slug).write_text(body, encoding="utf-8")
    return {"slug": slug, "name": name.strip(), "tags": taglist}


def _read(slug: str) -> str:
    try:
        return _path(slug).read_text(encoding="utf-8")
    except OSError:
        return ""


def _name_of(text: str, slug: str) -> str:
    m = re.search(r"component:\s*(.+?)\s*\|", text)
    return m.group(1).strip() if m else slug.replace("-", " ")


def _resolve_slug(name: str) -> str | None:
    """Find the stored slug for a spoken name, leniently (so 'hero' finds 'hero-section')."""
    slug = _slug(name)
    if _path(slug).exists():
        return slug
    comps = components()
    for c in comps:
        if c["name"].lower() == name.strip().lower():
            return c["slug"]
    qwords = set(w for w in slug.split("-") if w)
    for c in comps:
        cs = set(w for w in c["slug"].split("-") if w)
        if qwords and (qwords <= cs or cs <= qwords):
            return c["slug"]
    return None


def get(name: str) -> str | None:
    """Return the raw code of a saved component (the content of its ```html block)."""
    slug = _resolve_slug(name)
    if not slug:
        return None
    text = _read(slug)
    m = re.search(r"```html\n(.*?)\n```", text, re.S)
    return m.group(1) if m else None


def components() -> list[dict]:
    if not COMPONENTS_DIR.exists():
        return []
    out = []
    for p in sorted(COMPONENTS_DIR.glob("*.md")):
        text = p.read_text(encoding="utf-8", errors="ignore")
        tags = re.search(r"tags:\s*(.*?)\s*-->", text)
        out.append({"slug": p.stem, "name": _name_of(text, p.stem),
                    "tags": tags.group(1).strip() if tags else ""})
    return out


def search(query: str) -> list[dict]:
    q = (query or "").lower().split()
    if not q:
        return components()
    return [c for c in components()
            if all(w in (c["name"] + " " + c["tags"]).lower() for w in q)]


def remove(name: str) -> bool:
    slug = _resolve_slug(name)
    if slug and _path(slug).exists():
        _path(slug).unlink()
        return True
    return False


def use_name(text: str) -> str | None:
    """If the text asks to build with a saved component, return that component's name."""
    m = _USE.search((text or "").strip())
    return m.group("name").strip() if m else None


def save_name(text: str) -> str | None:
    m = _SAVE.search((text or "").strip())
    return m.group("name").strip() if m else None


def use_prompt(name: str, code: str, request: str = "") -> str:
    """Instruction: reuse the saved component's code in what we're building."""
    extra = f" The user said: {request}" if request else ""
    return (
        f"Build with my saved '{name}' component. Here is its exact code — reuse it as the basis, "
        f"adapting content and styling to fit the current page rather than writing a new one from "
        f"scratch:\n\n```html\n{code}\n```\n"
        f"Keep its structure and classes consistent with how I build.{extra}"
    )


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jarvis.components", description="Reusable UI component library")
    sub = ap.add_subparsers(dest="cmd")
    sv = sub.add_parser("save"); sv.add_argument("name"); sv.add_argument("--tags", default="")
    sh = sub.add_parser("show"); sh.add_argument("name")
    sub.add_parser("list")
    se = sub.add_parser("search"); se.add_argument("query", nargs="+")
    rm = sub.add_parser("remove"); rm.add_argument("name")

    args = ap.parse_args(argv)
    if args.cmd == "save":
        code = sys.stdin.read()
        if not code.strip():
            print("No code on stdin to save."); return 1
        info = save(args.name, code, args.tags)
        print(f"Saved component: {info['name']} ({info['slug']}).")
        return 0
    if args.cmd == "show":
        code = get(args.name)
        print(code or f"No component {args.name!r}.")
        return 0 if code else 1
    if args.cmd == "search":
        hits = search(" ".join(args.query))
        print("\n".join(f"  {c['slug']:24} {c['name']}  [{c['tags']}]" for c in hits) or "No matches.")
        return 0
    if args.cmd == "remove":
        print("Removed." if remove(args.name) else f"No component {args.name!r}.")
        return 0
    items = components()
    if not items:
        print("No saved components yet. Say \"save this as my hero section\".")
        return 0
    for c in items:
        print(f"  {c['slug']:24} {c['name']}  [{c['tags']}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
