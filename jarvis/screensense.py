"""Offline screen sense (#69): understand the screen with no cloud.

JARVIS normally reads the screen text-first (window tree -> OCR -> screenshot) and sends
it to the brain. This module adds a fully-local path for when you're off the grid or
saving usage: it captures the screen text locally and answers the common questions about
it — "what's on my screen", "read it", "is there an error" — with heuristics and no
Claude call. When a local vision model is configured (an Ollama vision model such as
llava), it can caption an actual screenshot too.

    python -m jarvis.screensense describe   # summarise the active window, locally
"""
from __future__ import annotations

import argparse
import re
import sys

_TRIGGER = re.compile(
    r"^(?:/screen|what(?:'s| is)?\s+on\s+(?:my\s+)?screen|read (?:my |the )?screen|"
    r"what does (?:this|the screen) say|read (?:this|it) (?:out|to me)|"
    r"is there an error|what'?s this error|what am i looking at|describe (?:my |the )?screen)\W*$", re.I)

_ERROR_RE = re.compile(
    r"\b(error|exception|traceback|failed|failure|cannot|can't|denied|unauthori[sz]ed|"
    r"not found|404|500|segfault|fatal|panic|refused|timed out|timeout|crash)\b", re.I)


def is_trigger(text: str) -> bool:
    return bool(_TRIGGER.match((text or "").strip()))


def wants_error_check(text: str) -> bool:
    return bool(re.search(r"\berror|wrong|problem|fail", (text or "").lower()))


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def find_error(text: str) -> str | None:
    """Return the first error-looking line on screen, or None."""
    for ln in _lines(text):
        if _ERROR_RE.search(ln):
            return ln[:200]
    return None


def describe(text: str, max_sentences: int = 3) -> str:
    """A short local summary of screen text — no model call."""
    lines = _lines(text)
    if not lines:
        return "I can't read any text on the screen right now."
    err = find_error(text)
    head = lines[0][:120]
    out = [f"The screen shows: {head}."]
    if len(lines) > 1:
        out.append(f"There are about {len(lines)} lines of text.")
    if err:
        out.append(f"I can see what looks like an error: \"{err}\".")
    return " ".join(out[:max_sentences])


def answer(question: str, text: str) -> str:
    """Answer a simple screen question locally. Falls back to a short description."""
    q = (question or "").lower()
    if wants_error_check(q):
        err = find_error(text)
        return (f"Yes — there's an error on screen: \"{err}\"." if err
                else "I don't see anything that looks like an error on screen.")
    if re.search(r"\bread\b", q):
        lines = _lines(text)
        snippet = " ".join(lines)[:400]
        return snippet or "There's no readable text on the screen."
    return describe(text)


def read_screen_text() -> str:
    """Pull the active window's text locally via the desktop MCP helpers, best-effort."""
    try:
        from .desktop_mcp import server as ds
        for fn in ("active_window_text", "ocr_screen"):
            f = getattr(ds, fn, None)
            if callable(f):
                try:
                    out = f()
                    if isinstance(out, str) and out.strip():
                        return out
                except Exception:
                    continue
    except Exception:
        pass
    return ""


def caption_via_ollama(image_path: str, model: str, host: str = "http://localhost:11434") -> str | None:
    """Caption a screenshot with a local Ollama vision model (e.g. llava), if available."""
    try:
        import base64
        import json
        import urllib.request
        data = base64.b64encode(open(image_path, "rb").read()).decode("ascii")
        payload = json.dumps({"model": model, "prompt": "Describe what is on this screen concisely.",
                              "images": [data], "stream": False}).encode("utf-8")
        req = urllib.request.Request(f"{host}/api/generate", data=payload,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read()).get("response", "").strip() or None
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jarvis.screensense", description="Local, offline screen understanding")
    ap.add_argument("cmd", nargs="?", default="describe", choices=["describe", "errors", "read"])
    args = ap.parse_args(argv)
    text = read_screen_text()
    if args.cmd == "errors":
        err = find_error(text)
        print(err or "No error found on screen.")
    elif args.cmd == "read":
        print(answer("read it", text))
    else:
        print(describe(text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
