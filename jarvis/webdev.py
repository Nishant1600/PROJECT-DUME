"""Website-development helpers for JARVIS (features #34, #35, #41).

The brain (Claude) can already write code and run npm/git through its Bash tool, so
rather than add MCP tools (which would bloat every prompt), this exposes a few reliable
building blocks as a tiny CLI the brain calls on demand:

    python -m jarvis.webdev stacks                 # list the scaffolds I know
    python -m jarvis.webdev scaffold <name> [stack] [--dir DIR] [--run]
    python -m jarvis.webdev preview-info <path>    # how to start the dev server + its URL
    python -m jarvis.webdev seo <path-or-index.html>   # on-page SEO / quality audit

#34 scaffold  - start a new site in the user's preferred stack (from memory/design.md).
#35 preview   - tell the brain the dev command + URL so it can open and screenshot the
                running site and self-review it against the user's rules.
#41 seo       - a dependency-free on-page SEO audit (title, meta, OG, headings, alt,
                canonical, JSON-LD, viewport, lang) with concrete fixes and a score.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from .config import MEMORY_DIR, resolve_path

# --------------------------------------------------------------------------- stacks (#34)

STACKS: dict[str, dict] = {
    "vite-react": {
        "aliases": ["react", "react-ts", "vite", "vite-react-ts"],
        "cmd": 'npm create vite@latest {name} -- --template react-ts',
        "port": 5173, "dev": "npm install && npm run dev",
    },
    "vite-vanilla": {
        "aliases": ["vanilla", "vanilla-ts", "plain-vite"],
        "cmd": 'npm create vite@latest {name} -- --template vanilla-ts',
        "port": 5173, "dev": "npm install && npm run dev",
    },
    "next": {
        "aliases": ["nextjs", "next.js", "next-app"],
        "cmd": 'npx --yes create-next-app@latest {name} --ts --eslint --app --tailwind --use-npm --no-src-dir --yes',
        "port": 3000, "dev": "npm run dev",
    },
    "astro": {
        "aliases": ["astrojs"],
        "cmd": 'npm create astro@latest {name} -- --template basics --typescript strict --no-git --yes',
        "port": 4321, "dev": "npm install && npm run dev",
    },
    "static": {
        "aliases": ["html", "plain", "vanilla-html"],
        "cmd": "",   # no generator; we write a minimal index.html ourselves
        "port": 8000, "dev": "python -m http.server 8000",
    },
}
DEFAULT_STACK = "vite-react"

STATIC_INDEX = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <meta name="description" content="{title} - built with JARVIS." />
  <link rel="icon" href="data:," />
</head>
<body>
  <main>
    <h1>{title}</h1>
    <p>Edit <code>index.html</code> to begin.</p>
  </main>
</body>
</html>
"""


def _canon_stack(stack: str | None) -> str | None:
    if not stack:
        return None
    s = stack.strip().lower()
    if s in STACKS:
        return s
    for key, meta in STACKS.items():
        if s in meta["aliases"]:
            return key
    return None


def preferred_stack() -> str:
    """Guess the user's preferred stack from learned design notes / rules; else the default."""
    text = ""
    for name in ("design.md", "rules.md"):
        p = MEMORY_DIR / name
        if p.exists():
            text += "\n" + p.read_text(encoding="utf-8", errors="ignore").lower()
    # most specific first
    for key in ("next", "astro", "vite-react", "vite-vanilla", "static"):
        if _canon_stack(key) and (key in text or any(a in text for a in STACKS[key]["aliases"])):
            return key
    return DEFAULT_STACK


def scaffold(name: str, stack: str | None, directory: str | None, run: bool) -> dict:
    key = _canon_stack(stack) or preferred_stack()
    meta = STACKS[key]
    base = resolve_path(directory) if directory else resolve_path("workspace")
    base.mkdir(parents=True, exist_ok=True)
    target = base / name
    result = {"stack": key, "name": name, "path": str(target), "port": meta["port"],
              "dev": meta["dev"], "ran": False}

    if key == "static":
        result["command"] = f"(write {target}/index.html)"
        if run:
            target.mkdir(parents=True, exist_ok=True)
            (target / "index.html").write_text(
                STATIC_INDEX.format(title=name), encoding="utf-8")
            result["ran"] = True
        return result

    cmd = meta["cmd"].format(name=name)
    result["command"] = cmd
    if run:
        proc = subprocess.run(cmd, cwd=str(base), shell=True, capture_output=True, text=True)
        result["ran"] = True
        result["returncode"] = proc.returncode
        result["output"] = (proc.stdout + proc.stderr)[-2000:]
    return result


def dev_command(path: str) -> dict:
    """How to start the dev server for an existing project, and the URL it serves (#35)."""
    root = resolve_path(path)
    pkg = root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
        scripts = data.get("scripts", {}) or {}
        deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
        if "next" in deps:
            port = 3000
        elif "astro" in deps:
            port = 4321
        else:
            port = 5173   # vite and most others
        if "dev" in scripts:
            return {"command": "npm run dev", "url": f"http://localhost:{port}", "cwd": str(root)}
        if "start" in scripts:
            return {"command": "npm start", "url": f"http://localhost:{port}", "cwd": str(root)}
    if (root / "index.html").exists():
        return {"command": "python -m http.server 8000", "url": "http://localhost:8000",
                "cwd": str(root)}
    return {"command": None, "url": None, "cwd": str(root),
            "note": "No package.json dev/start script and no index.html found."}


# --------------------------------------------------------------------------- SEO audit (#41)

@dataclass
class _Doc:
    lang: str = ""
    title: str = ""
    metas: dict = field(default_factory=dict)      # name/property (lower) -> content
    links: dict = field(default_factory=dict)      # rel (lower) -> href
    headings: list = field(default_factory=list)   # (level, text)
    imgs_total: int = 0
    imgs_no_alt: int = 0
    jsonld: list = field(default_factory=list)      # raw json-ld strings


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.doc = _Doc()
        self._grab: str | None = None   # current heading level being captured
        self._in_title = False
        self._in_ld = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.doc.lang = a.get("lang", "")
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key:
                self.doc.metas[key] = a.get("content", "")
        elif tag == "link":
            rel = (a.get("rel") or "").lower()
            if rel:
                self.doc.links[rel] = a.get("href", "")
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._grab = tag
            self.doc.headings.append([int(tag[1]), ""])
        elif tag == "img":
            self.doc.imgs_total += 1
            if "alt" not in a:          # alt="" is valid (decorative); a missing attr is not
                self.doc.imgs_no_alt += 1
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_ld = True
            self.doc.jsonld.append("")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._grab = None
        elif tag == "script" and self._in_ld:
            self._in_ld = False

    def handle_data(self, data):
        if self._in_title:
            self.doc.title += data
        elif self._grab and self.doc.headings:
            self.doc.headings[-1][1] += data
        elif self._in_ld and self.doc.jsonld:
            self.doc.jsonld[-1] += data


def seo_audit(path_or_html: str) -> dict:
    p = resolve_path(path_or_html)
    if p.is_dir():
        p = p / "index.html"
    if p.exists():
        html = p.read_text(encoding="utf-8", errors="ignore")
        source = str(p)
    else:
        html = path_or_html   # treat the argument as raw HTML
        source = "(inline html)"

    parser = _Parser()
    parser.feed(html)
    d = parser.doc
    findings: list[dict] = []

    def add(status: str, check: str, detail: str, fix: str = "") -> None:
        findings.append({"status": status, "check": check, "detail": detail, "fix": fix})

    # lang
    add("pass" if d.lang else "fail", "html lang",
        f'lang="{d.lang}"' if d.lang else "missing",
        "" if d.lang else 'Add <html lang="en"> for accessibility and SEO.')
    # title
    t = d.title.strip()
    if not t:
        add("fail", "title", "missing", "Add a <title>, ~50-60 characters.")
    elif not (10 <= len(t) <= 60):
        add("warn", "title", f'"{t}" ({len(t)} chars)',
            "Aim for 10-60 characters so it isn't truncated in search results.")
    else:
        add("pass", "title", f'"{t}" ({len(t)} chars)')
    # meta description
    desc = d.metas.get("description", "").strip()
    if not desc:
        add("fail", "meta description", "missing",
            "Add <meta name=\"description\"> of ~120-155 characters.")
    elif not (50 <= len(desc) <= 160):
        add("warn", "meta description", f"{len(desc)} chars",
            "Aim for 50-160 characters.")
    else:
        add("pass", "meta description", f"{len(desc)} chars")
    # viewport
    add("pass" if "viewport" in d.metas else "fail", "viewport",
        d.metas.get("viewport", "missing"),
        "" if "viewport" in d.metas else 'Add <meta name="viewport" content="width=device-width, initial-scale=1">.')
    # single h1
    h1s = [h for h in d.headings if h[0] == 1]
    if len(h1s) == 1:
        add("pass", "h1", "exactly one")
    elif not h1s:
        add("fail", "h1", "none", "Add a single, descriptive <h1>.")
    else:
        add("warn", "h1", f"{len(h1s)} found", "Use exactly one <h1> per page.")
    # heading order (no skipped levels going down)
    skipped = False
    prev = 0
    for level, _ in d.headings:
        if prev and level > prev + 1:
            skipped = True
            break
        prev = level
    if d.headings:
        add("pass" if not skipped else "warn", "heading order",
            "logical" if not skipped else "a level is skipped",
            "" if not skipped else "Don't jump heading levels (e.g. h2 -> h4); keep them sequential.")
    # image alt
    if d.imgs_total == 0:
        add("pass", "image alt", "no images")
    elif d.imgs_no_alt == 0:
        add("pass", "image alt", f"all {d.imgs_total} images have alt")
    else:
        add("warn", "image alt", f"{d.imgs_no_alt}/{d.imgs_total} missing alt",
            "Add descriptive alt text to every <img> (alt=\"\" for decorative ones).")
    # Open Graph
    og = [k for k in ("og:title", "og:description", "og:image") if d.metas.get(k)]
    if len(og) == 3:
        add("pass", "Open Graph", "title, description, image")
    else:
        missing = [k for k in ("og:title", "og:description", "og:image") if k not in og]
        add("warn", "Open Graph", f"missing {', '.join(missing)}",
            "Add og:title, og:description and og:image for rich link previews.")
    # Twitter card
    add("pass" if d.metas.get("twitter:card") else "warn", "Twitter card",
        d.metas.get("twitter:card", "missing"),
        "" if d.metas.get("twitter:card") else 'Add <meta name="twitter:card" content="summary_large_image">.')
    # canonical
    add("pass" if d.links.get("canonical") else "warn", "canonical",
        d.links.get("canonical", "missing"),
        "" if d.links.get("canonical") else "Add <link rel=\"canonical\"> to avoid duplicate-content issues.")
    # JSON-LD
    if not d.jsonld:
        add("warn", "structured data", "no JSON-LD",
            "Add a JSON-LD <script type=\"application/ld+json\"> (e.g. WebSite / Organization).")
    else:
        bad = [s for s in d.jsonld if _bad_json(s)]
        if bad:
            add("fail", "structured data", f"{len(bad)} invalid JSON-LD block(s)",
                "Fix the JSON so it parses.")
        else:
            add("pass", "structured data", f"{len(d.jsonld)} JSON-LD block(s)")
    # favicon
    has_icon = any("icon" in rel for rel in d.links)
    add("pass" if has_icon else "warn", "favicon",
        "present" if has_icon else "missing",
        "" if has_icon else 'Add <link rel="icon" href="...">.')

    passes = sum(1 for f in findings if f["status"] == "pass")
    fails = sum(1 for f in findings if f["status"] == "fail")
    warns = sum(1 for f in findings if f["status"] == "warn")
    score = round(100 * passes / len(findings)) if findings else 0
    return {"source": source, "score": score, "pass": passes, "warn": warns,
            "fail": fails, "findings": findings}


def _bad_json(s: str) -> bool:
    s = s.strip()
    if not s:
        return True
    try:
        json.loads(s)
        return False
    except ValueError:
        return True


# --------------------------------------------------------------------------- CLI

def _print_seo(rep: dict) -> None:
    mark = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}
    print(f"SEO audit of {rep['source']}  -  score {rep['score']}/100  "
          f"({rep['pass']} pass, {rep['warn']} warn, {rep['fail']} fail)\n")
    for f in rep["findings"]:
        line = f"  [{mark[f['status']]}] {f['check']}: {f['detail']}"
        print(line)
        if f["fix"] and f["status"] != "pass":
            print(f"         fix: {f['fix']}")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):   # avoid cp1252 crashes when the brain reads our output
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jarvis.webdev", description="JARVIS website-dev helpers")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("stacks", help="list known scaffolds")

    sp = sub.add_parser("scaffold", help="start a new site")
    sp.add_argument("name")
    sp.add_argument("stack", nargs="?", default=None)
    sp.add_argument("--dir", default=None)
    sp.add_argument("--run", action="store_true", help="actually run it (otherwise print the command)")

    pp = sub.add_parser("preview-info", help="dev command + URL for an existing project")
    pp.add_argument("path")

    se = sub.add_parser("seo", help="on-page SEO audit of an HTML file or folder")
    se.add_argument("path")
    se.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "stacks":
        print(f"Preferred (from your design notes): {preferred_stack()}\n")
        for key, meta in STACKS.items():
            print(f"  {key:14} aliases: {', '.join(meta['aliases'])}")
        return 0
    if args.cmd == "scaffold":
        res = scaffold(args.name, args.stack, args.dir, args.run)
        print(json.dumps(res, indent=2))
        return 0 if res.get("returncode", 0) == 0 else 1
    if args.cmd == "preview-info":
        print(json.dumps(dev_command(args.path), indent=2))
        return 0
    if args.cmd == "seo":
        rep = seo_audit(args.path)
        if args.json:
            print(json.dumps(rep, indent=2))
        else:
            _print_seo(rep)
        return 0 if rep["fail"] == 0 else 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
