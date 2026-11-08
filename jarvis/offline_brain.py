"""Zero-cloud fallback for JARVIS (README roadmap: "a small on-device model fields the basics
when offline or saving usage").

Two jobs:
  * basics_answer() answers true trivia — time, date, a greeting, simple arithmetic, "who are
    you", "what can you do" — with no model call at all. Used even when online, so these never
    cost a turn on your Claude plan.
  * answer() is the fallback when the cloud brain can't be reached: it tries a small local model
    through Ollama (http://localhost:11434) if you have one, and otherwise falls back to the
    same basics, so JARVIS still responds instead of erroring.

Nothing here needs the network. Ollama is optional; if it isn't running, the basics still work.
"""
from __future__ import annotations

import datetime
import json
import re
import urllib.request

OLLAMA_URL = "http://localhost:11434/api/generate"

_NET_HINTS = ("network", "connection", "connect", "timed out", "timeout", "unreachable",
              "getaddrinfo", "enotfound", "failed to fetch", "fetch failed", "offline",
              "resolve host", "name resolution", "temporarily unavailable", "dns",
              "econnrefused", "no address")


def is_offline_error(err: str | None) -> bool:
    e = (err or "").lower()
    return any(h in e for h in _NET_HINTS)


# --------------------------------------------------------------------------- basics (no model)

_WORD_OPS = {"plus": "+", "add": "+", "minus": "-", "subtract": "-", "times": "*",
             "multiplied by": "*", "x": "*", "divided by": "/", "over": "/"}


def basics_answer(text: str, cfg: dict | None = None) -> str | None:
    """Answer only clear trivia, with no model call. Returns None for anything else."""
    t = " ".join((text or "").strip().split())
    low = t.lower().rstrip("?.!")
    if not low:
        return None
    who = ((cfg or {}).get("address_as") or "sir").capitalize()

    # time / date / day
    if re.fullmatch(r"(what(?:'s| is)?\s+(?:the\s+)?time|what\s+time\s+is\s+it|the\s+time)", low):
        return f"It's {datetime.datetime.now().strftime('%I:%M %p').lstrip('0')}, {who.lower()}."
    if re.fullmatch(r"what(?:'s| is)?\s+(?:the\s+|today'?s\s+)?date|what(?:'s| is)?\s+today", low):
        return f"It's {datetime.date.today().strftime('%A, %d %B %Y')}."
    if re.fullmatch(r"what\s+day\s+is\s+it(?:\s+today)?", low):
        return f"It's {datetime.date.today().strftime('%A')}."

    # greeting (note: "good morning" is the brief trigger, handled elsewhere)
    if re.fullmatch(r"(hi|hello|hey|yo|hiya)(?:\s+jarvis)?", low):
        return f"Hello, {who.lower()}. How can I help?"

    # simple two-number arithmetic
    ans = _arith(low)
    if ans is not None:
        return ans

    # identity / capabilities
    if re.fullmatch(r"(who|what)\s+are\s+you|what(?:'s| is)?\s+your\s+name", low):
        return "I'm JARVIS, your assistant. I listen, talk back, and help run your PC."
    if re.fullmatch(r"what\s+can\s+you\s+do|what\s+are\s+you\s+able\s+to\s+do", low):
        return ("I can check your mail and calendar, track deadlines, build and review code, "
                "answer from your notes, and run things on your PC by voice. Just ask.")
    return None


def _arith(low: str) -> str | None:
    m = re.fullmatch(
        r"(?:what(?:'s| is)?\s+|calculate\s+|compute\s+)?"
        r"(-?\d+(?:\.\d+)?)\s*(\+|-|\*|/|x|plus|minus|times|multiplied by|divided by|add|over)\s*"
        r"(-?\d+(?:\.\d+)?)", low)
    if not m:
        return None
    a, op_raw, b = m.group(1), m.group(2), m.group(3)
    op = _WORD_OPS.get(op_raw, op_raw)
    try:
        x, y = float(a), float(b)
        if op == "+":
            r = x + y
        elif op == "-":
            r = x - y
        elif op == "*":
            r = x * y
        elif op == "/":
            if y == 0:
                return "You can't divide by zero."
            r = x / y
        else:
            return None
    except ValueError:
        return None
    r = int(r) if r == int(r) else round(r, 4)
    return f"That's {r}."


# --------------------------------------------------------------------------- local model (Ollama)

def _ollama_models(timeout: float = 1.5) -> list[str]:
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=timeout) as resp:
            data = json.loads(resp.read())
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return []


def ollama_answer(text: str, model: str | None = None, persona: str = "", timeout: float = 30) -> str | None:
    models = _ollama_models()
    if not models:
        return None
    chosen = model if (model and any(m.startswith(model) for m in models)) else models[0]
    system = persona or ("You are JARVIS, a concise, calm voice assistant. Answer in one to three "
                         "short spoken sentences. You are offline, so you can't use the internet, "
                         "email, or files — say so if asked for those.")
    body = json.dumps({"model": chosen, "prompt": text, "system": system, "stream": False,
                       "options": {"num_predict": 220}}).encode("utf-8")
    try:
        req = urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out = json.loads(resp.read()).get("response", "").strip()
        return out or None
    except Exception:
        return None


def available(cfg: dict | None = None) -> bool:
    """True if some offline capability exists (always true — basics need nothing)."""
    return True


def answer(text: str, cfg: dict | None = None, persona: str = "") -> str:
    """Best offline answer: basics first (free), then a local model, then a plain apology."""
    basic = basics_answer(text, cfg)
    if basic:
        return basic
    off = (cfg or {}).get("offline") or {}
    if off.get("enabled", True):
        local = ollama_answer(text, off.get("model"), persona)
        if local:
            return local
    return ("I can't reach the network right now, so I can only handle simple things like the "
            "time, quick maths, or questions about what I can do. Try me again once you're back online.")


# --------------------------------------------------------------------------- CLI (debug)

def main(argv: list[str] | None = None) -> int:
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    q = " ".join(sys.argv[1:]) or "what time is it"
    print("basics:", basics_answer(q))
    print("ollama models:", _ollama_models())
    print("answer:", answer(q))
    return 0


if __name__ == "__main__":
    sys.exit(main())
