"""Performance budget check (#65): catch weight problems before you ship.

A dependency-free, local heuristic pass over a built page: total page weight (HTML +
the local CSS/JS/images it references), oversized images, render-blocking scripts in
the <head>, stylesheet count, images missing width/height (layout shift), and heavy
inline blocks. It measures real file sizes on disk — no network, no headless browser —
so it runs anywhere and is a fast gut-check against a Lighthouse-style budget.

    python -m jarvis.perf <path-or-index.html> [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from .config import resolve_path

DEFAULT_BUDGETS = {
    "page_kb": 1600,        # total transferred weight target
    "image_kb": 200,        # per-image target
    "scripts": 10,          # total <script> tags
    "stylesheets": 4,       # total external stylesheets
    "blocking_scripts": 0,  # render-blocking scripts in <head> (no async/defer)
}


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[dict] = []
        self.styles: list[str] = []            # external stylesheet hrefs
        self.imgs: list[dict] = []
        self.inline_script = 0
        self.inline_style = 0
        self._in_head = True
        self._grab = None                      # "script" | "style" while capturing inline content
        self._cur_async = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script":
            if a.get("src"):
                self.scripts.append({"src": a["src"], "in_head": self._in_head,
                                     "defer": "defer" in a, "async": "async" in a,
                                     "module": a.get("type", "") == "module"})
            else:
                self._grab = "script"
        elif tag == "link" and "stylesheet" in (a.get("rel") or "").lower():
            if a.get("href"):
                self.styles.append(a["href"])
        elif tag == "style":
            self._grab = "style"
        elif tag == "img":
            self.imgs.append({"src": a.get("src", ""), "has_w": "width" in a, "has_h": "height" in a,
                              "lazy": a.get("loading", "") == "lazy"})

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "head":
            self._in_head = False
        if tag == self._grab:
            self._grab = None

    def handle_data(self, data):
        if self._grab == "script":
            self.inline_script += len(data.encode("utf-8"))
        elif self._grab == "style":
            self.inline_style += len(data.encode("utf-8"))


def _local_size(ref: str, base: Path) -> int | None:
    """Size in bytes of a locally-referenced asset, or None if remote/absent."""
    if not ref:
        return None
    parsed = urlparse(ref)
    if parsed.scheme in ("http", "https", "data") or ref.startswith("//"):
        return None
    rel = ref.split("?")[0].split("#")[0].lstrip("/")
    f = (base / rel)
    try:
        return f.stat().st_size if f.is_file() else None
    except OSError:
        return None


def perf_audit(path_or_html: str, budgets: dict | None = None) -> dict:
    budgets = {**DEFAULT_BUDGETS, **(budgets or {})}
    p = resolve_path(path_or_html)
    if p.is_dir():
        p = p / "index.html"
    if p.exists():
        html = p.read_text(encoding="utf-8", errors="ignore")
        base = p.parent
        source = str(p)
        html_bytes = p.stat().st_size
    else:
        html = path_or_html
        base = Path(".")
        source = "(inline html)"
        html_bytes = len(html.encode("utf-8"))

    d = _Parser()
    d.feed(html)
    findings: list[dict] = []

    def add(status, check, detail, fix=""):
        findings.append({"status": status, "check": check, "detail": detail, "fix": fix})

    # total weight from local assets we can measure
    total = html_bytes
    measured = unknown = 0
    big_images = []
    for ref in d.styles + [s["src"] for s in d.scripts]:
        sz = _local_size(ref, base)
        if sz is None:
            unknown += 1
        else:
            total += sz
            measured += 1
    for img in d.imgs:
        sz = _local_size(img["src"], base)
        if sz is None:
            unknown += 1
        else:
            total += sz
            measured += 1
            if sz > budgets["image_kb"] * 1024:
                big_images.append((img["src"], sz))

    total_kb = round(total / 1024)
    note = f" (+{unknown} remote/unmeasured)" if unknown else ""
    if total_kb <= budgets["page_kb"]:
        add("pass", "page weight", f"{total_kb} KB measured{note}")
    else:
        add("warn" if total_kb <= budgets["page_kb"] * 1.5 else "fail", "page weight",
            f"{total_kb} KB measured{note} — budget {budgets['page_kb']} KB",
            "Trim/compress assets, split code, or lazy-load below-the-fold content.")

    # oversized images
    if big_images:
        worst = max(sz for _, sz in big_images)
        add("warn", "image size", f"{len(big_images)} image(s) over {budgets['image_kb']} KB "
            f"(largest {round(worst/1024)} KB)",
            "Compress and resize; prefer WebP/AVIF and serve responsive sizes.")
    else:
        add("pass", "image size", "no oversized local images")

    # render-blocking scripts in head
    blocking = [s for s in d.scripts if s["in_head"] and not s["defer"]
                and not s["async"] and not s["module"]]
    if len(blocking) <= budgets["blocking_scripts"]:
        add("pass", "render-blocking js", "none in <head>")
    else:
        add("warn", "render-blocking js", f"{len(blocking)} blocking script(s) in <head>",
            "Add defer/async, use type=module, or move <script> to the end of <body>.")

    # script / stylesheet counts
    add("pass" if len(d.scripts) <= budgets["scripts"] else "warn", "script count",
        f"{len(d.scripts)} external script(s)",
        "" if len(d.scripts) <= budgets["scripts"] else "Bundle/split to cut request count.")
    add("pass" if len(d.styles) <= budgets["stylesheets"] else "warn", "stylesheet count",
        f"{len(d.styles)} stylesheet(s)",
        "" if len(d.styles) <= budgets["stylesheets"] else "Combine stylesheets to reduce requests.")

    # layout shift: images without explicit dimensions
    no_dims = [i for i in d.imgs if not (i["has_w"] and i["has_h"])]
    if d.imgs and no_dims:
        add("warn", "layout shift", f"{len(no_dims)}/{len(d.imgs)} images without width+height",
            "Set width and height (or aspect-ratio) on <img> to reserve space and avoid CLS.")
    else:
        add("pass", "layout shift", "images have dimensions" if d.imgs else "no images")

    # heavy inline blocks
    inline_kb = round((d.inline_script + d.inline_style) / 1024)
    if inline_kb > 50:
        add("warn", "inline code", f"{inline_kb} KB inline script/style",
            "Move large inline code to cacheable external files.")
    else:
        add("pass", "inline code", f"{inline_kb} KB inline")

    passes = sum(1 for f in findings if f["status"] == "pass")
    fails = sum(1 for f in findings if f["status"] == "fail")
    warns = sum(1 for f in findings if f["status"] == "warn")
    score = round(100 * passes / len(findings)) if findings else 0
    return {"source": source, "score": score, "total_kb": total_kb, "measured": measured,
            "unmeasured": unknown, "pass": passes, "warn": warns, "fail": fails,
            "findings": findings}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jarvis.perf", description="Local performance-budget check")
    ap.add_argument("path")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rep = perf_audit(args.path)
    if args.json:
        print(json.dumps(rep, indent=2))
        return 0 if rep["fail"] == 0 else 2
    mark = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}
    print(f"Performance check of {rep['source']}  -  score {rep['score']}/100  "
          f"(~{rep['total_kb']} KB measured, {rep['unmeasured']} unmeasured)\n")
    for f in rep["findings"]:
        print(f"  [{mark[f['status']]}] {f['check']}: {f['detail']}")
        if f["fix"] and f["status"] != "pass":
            print(f"         fix: {f['fix']}")
    return 0 if rep["fail"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
