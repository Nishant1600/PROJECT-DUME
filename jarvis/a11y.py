"""Accessibility (a11y) audit (#64): the sibling of the SEO audit.

A dependency-free, on-page accessibility check — missing alt text, unlabelled form
controls, links/buttons with no accessible name, missing landmarks, zoom disabled,
positive tabindex, and (where colours are set inline) WCAG contrast. Same report shape
and scoring as the SEO audit, with concrete fixes.

    python -m jarvis.a11y <path-or-index.html> [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from html.parser import HTMLParser

from .config import resolve_path

_NO_LABEL_NEEDED = {"hidden", "submit", "button", "reset", "image"}
_INTERACTIVE_TEXT = {"a", "button"}


def _parse_hex(h: str):
    h = (h or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6 or re.search(r"[^0-9a-fA-F]", h):
        return None
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lum(rgb) -> float:
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast_ratio(hex1: str, hex2: str):
    """WCAG contrast ratio between two hex colours (1-21), or None if unparseable."""
    a, b = _parse_hex(hex1), _parse_hex(hex2)
    if not a or not b:
        return None
    l1, l2 = _lum(a), _lum(b)
    hi, lo = max(l1, l2), min(l1, l2)
    return round((hi + 0.05) / (lo + 0.05), 2)


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.lang = ""
        self.title = ""
        self._in_title = False
        self.ids: set[str] = set()
        self.labels_for: set[str] = set()
        self.imgs_total = 0
        self.imgs_no_alt = 0
        self.controls: list[dict] = []       # form controls needing a label
        self.interactive: list[dict] = []     # links/buttons needing an accessible name
        self.landmarks: set[str] = set()
        self.tabindex: list[int] = []
        self.viewport = ""
        self.bad_contrast: list[tuple] = []
        self._stack: list[str] = []
        self._cur = None                      # currently open interactive element dict

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        self._stack.append(tag)
        if a.get("id"):
            self.ids.add(a["id"])
        if a.get("tabindex"):
            try:
                self.tabindex.append(int(a["tabindex"]))
            except ValueError:
                pass
        # inline contrast (both colour + background set on one element)
        style = a.get("style", "")
        fg = re.search(r"(?<!-)\bcolor\s*:\s*(#[0-9a-fA-F]{3,6})", style)
        bg = re.search(r"background(?:-color)?\s*:\s*(#[0-9a-fA-F]{3,6})", style)
        if fg and bg:
            r = contrast_ratio(fg.group(1), bg.group(1))
            if r is not None and r < 4.5:
                self.bad_contrast.append((fg.group(1), bg.group(1), r))

        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "title":
            self._in_title = True
        elif tag == "meta" and (a.get("name") or "").lower() == "viewport":
            self.viewport = a.get("content", "")
        elif tag == "label" and a.get("for"):
            self.labels_for.add(a["for"])
        elif tag == "img":
            self.imgs_total += 1
            if "alt" not in a:
                self.imgs_no_alt += 1
            if self._cur is not None and a.get("alt"):
                self._cur["has_text"] = True      # an image with alt names its link/button
        elif tag in ("input", "select", "textarea"):
            typ = a.get("type", "text").lower()
            if tag == "input" and typ in _NO_LABEL_NEEDED:
                pass
            else:
                labelled = bool(a.get("aria-label") or a.get("aria-labelledby") or a.get("title")
                                or (a.get("id") and a["id"] in self.labels_for)
                                or "label" in self._stack)
                self.controls.append({"tag": tag, "type": typ, "labelled": labelled,
                                      "id": a.get("id", "")})
        elif tag in ("main", "nav", "header", "footer", "aside"):
            self.landmarks.add(tag)
        if (a.get("role") or "").lower() in ("main", "navigation", "banner", "contentinfo"):
            self.landmarks.add(a["role"].lower())
        if tag in _INTERACTIVE_TEXT:
            # a link needs an href to be a link; an anchor without href is skipped
            if tag == "a" and "href" not in a:
                return
            self._cur = {"tag": tag, "text": "",
                         "has_text": bool(a.get("aria-label") or a.get("title"))}
            self.interactive.append(self._cur)

    def handle_endtag(self, tag):
        if self._stack and tag in self._stack:
            # pop back to and including the matching tag
            while self._stack and self._stack.pop() != tag:
                pass
        if tag == "title":
            self._in_title = False
        if tag in _INTERACTIVE_TEXT:
            self._cur = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._cur is not None and data.strip():
            self._cur["text"] += data
            self._cur["has_text"] = True


def a11y_audit(path_or_html: str) -> dict:
    p = resolve_path(path_or_html)
    if p.is_dir():
        p = p / "index.html"
    if p.exists():
        html = p.read_text(encoding="utf-8", errors="ignore")
        source = str(p)
    else:
        html = path_or_html
        source = "(inline html)"

    d = _Parser()
    d.feed(html)
    findings: list[dict] = []

    def add(status, check, detail, fix=""):
        findings.append({"status": status, "check": check, "detail": detail, "fix": fix})

    add("pass" if d.lang else "fail", "html lang", f'lang="{d.lang}"' if d.lang else "missing",
        "" if d.lang else 'Add <html lang="en"> so screen readers use the right pronunciation.')
    add("pass" if d.title.strip() else "fail", "page title",
        "present" if d.title.strip() else "missing",
        "" if d.title.strip() else "Give every page a unique, descriptive <title>.")

    # images
    if d.imgs_total == 0:
        add("pass", "image alt", "no images")
    elif d.imgs_no_alt == 0:
        add("pass", "image alt", f"all {d.imgs_total} images have alt")
    else:
        add("fail", "image alt", f"{d.imgs_no_alt}/{d.imgs_total} missing alt",
            'Every <img> needs alt (alt="" for purely decorative images).')

    # form controls
    unlabelled = [c for c in d.controls if not c["labelled"]]
    if not d.controls:
        add("pass", "form labels", "no form controls")
    elif not unlabelled:
        add("pass", "form labels", f"all {len(d.controls)} controls labelled")
    else:
        add("fail", "form labels", f"{len(unlabelled)}/{len(d.controls)} controls unlabelled",
            "Give each input a <label for> (or aria-label). Unlabelled fields are unusable by screen readers.")

    # interactive accessible names
    nameless = [e for e in d.interactive if not e["has_text"] and not e["text"].strip()]
    if not d.interactive:
        add("pass", "link/button names", "none found")
    elif not nameless:
        add("pass", "link/button names", f"all {len(d.interactive)} have text")
    else:
        add("fail", "link/button names", f"{len(nameless)} empty link(s)/button(s)",
            "Icon-only links/buttons need an aria-label (or visually-hidden text).")

    # landmarks
    if "main" in d.landmarks:
        add("pass", "landmarks", "has <main>" + (f" + {', '.join(sorted(d.landmarks - {'main'}))}"
                                                  if d.landmarks - {"main"} else ""))
    elif d.landmarks:
        add("warn", "landmarks", f"has {', '.join(sorted(d.landmarks))} but no <main>",
            "Wrap the primary content in <main> so users can skip to it.")
    else:
        add("warn", "landmarks", "none",
            "Use landmarks (<header>, <nav>, <main>, <footer>) so assistive tech can navigate.")

    # zoom disabled
    vp = d.viewport.lower()
    if "user-scalable=no" in vp or re.search(r"maximum-scale\s*=\s*1(?:\.0)?\b", vp):
        add("fail", "zoom", "pinch-zoom disabled",
            "Remove user-scalable=no / maximum-scale=1 — it blocks low-vision users from zooming.")
    else:
        add("pass", "zoom", "not disabled")

    # positive tabindex
    pos = [t for t in d.tabindex if t > 0]
    if pos:
        add("warn", "tabindex", f"{len(pos)} positive tabindex value(s)",
            "Avoid tabindex > 0; it breaks the natural focus order. Use 0 or restructure the DOM.")
    else:
        add("pass", "tabindex", "no positive tabindex")

    # contrast (only where set inline)
    if d.bad_contrast:
        worst = min(r for _, _, r in d.bad_contrast)
        add("warn", "colour contrast", f"{len(d.bad_contrast)} inline pair(s) below 4.5:1 (worst {worst}:1)",
            "Aim for at least 4.5:1 for body text (3:1 for large text).")
    else:
        add("pass", "colour contrast", "no low-contrast inline colours found")

    passes = sum(1 for f in findings if f["status"] == "pass")
    fails = sum(1 for f in findings if f["status"] == "fail")
    warns = sum(1 for f in findings if f["status"] == "warn")
    score = round(100 * passes / len(findings)) if findings else 0
    return {"source": source, "score": score, "pass": passes, "warn": warns,
            "fail": fails, "findings": findings}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jarvis.a11y", description="On-page accessibility audit")
    ap.add_argument("path")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rep = a11y_audit(args.path)
    if args.json:
        print(json.dumps(rep, indent=2))
        return 0 if rep["fail"] == 0 else 2
    mark = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}
    print(f"Accessibility audit of {rep['source']}  -  score {rep['score']}/100  "
          f"({rep['pass']} pass, {rep['warn']} warn, {rep['fail']} fail)\n")
    for f in rep["findings"]:
        print(f"  [{mark[f['status']]}] {f['check']}: {f['detail']}")
        if f["fix"] and f["status"] != "pass":
            print(f"         fix: {f['fix']}")
    return 0 if rep["fail"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
