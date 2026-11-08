"""Clipboard genie for JARVIS (README roadmap: "explain / translate / tidy what I just copied").

Say "explain what I just copied", "translate this to Hindi", "tidy up what I copied" and
JARVIS reads your clipboard, does it, and (for rewrites) can put the result back so you can
paste. Reading/writing the clipboard uses PowerShell's Get-/Set-Clipboard, so there's no extra
dependency.

    python -m jarvis.clipboard get          # print clipboard text
    echo "tidied text" | python -m jarvis.clipboard set
"""
from __future__ import annotations

import re
import subprocess
import sys

MAX_CLIP_CHARS = 6000

_TRIGGER = re.compile(
    r"\b(explain|translate|tidy|clean\s*up|rewrite|summari[sz]e|proof\s*read|proofread|fix|"
    r"improve|shorten|rephrase|define|what(?:'s| is) this)\b.*\b"
    r"(clip\s*board|what\s+(?:i|I)\s+(?:just\s+)?copied|(?:this|that)\s+(?:text|thing)?|copied\s+text)\b",
    re.I)
_SHORT = re.compile(r"^\s*(?:/clip|clipboard\s+genie|what(?:'?s| is)?\s+(?:on\s+)?my\s+clipboard)\s*$", re.I)


def is_trigger(text: str) -> bool:
    t = (text or "").strip()
    return bool(_SHORT.match(t) or _TRIGGER.search(t))


def get_text() -> str:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        return (out.stdout or "").strip()
    except Exception:
        return ""


def set_text(text: str) -> bool:
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard"],
                       input=text, text=True, encoding="utf-8", timeout=10, check=True)
        return True
    except Exception:
        return False


def build_prompt(user_text: str, clip: str) -> str:
    clip = clip[:MAX_CLIP_CHARS]
    truncated = " (truncated)" if len(clip) == MAX_CLIP_CHARS else ""
    return (
        f"The user said: \"{user_text.strip()}\".\n"
        f"Here is exactly what is on their clipboard{truncated}:\n\n"
        f"\"\"\"\n{clip}\n\"\"\"\n\n"
        "Do what they asked with this clipboard text. If they asked you to tidy, rewrite, fix, "
        "shorten, rephrase or translate it, output the finished result and also put it back on "
        "their clipboard by piping just the result to `python -m jarvis.clipboard set` via Bash, "
        "then tell them in one line it's ready to paste. If they asked you to explain, summarise "
        "or define it, answer in a sentence or two. Keep spoken replies short."
    )


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "get"
    if cmd == "set":
        return 0 if set_text(sys.stdin.read()) else 1
    text = get_text()
    if not text:
        print("(clipboard is empty or not text)")
        return 1
    print(text[:MAX_CLIP_CHARS])
    return 0


if __name__ == "__main__":
    sys.exit(main())
