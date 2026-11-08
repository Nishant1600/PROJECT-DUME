"""Make it yours: pick JARVIS's voice and wake word (README roadmap: "pick your wake word and your voice").

Voice: say "change your voice to a female British voice", "use an Indian English voice",
"speak faster". The change applies to the next thing JARVIS says and is saved to config.yaml.
Wake word: say "change the wake word to Alexa" (or point at a custom openWakeWord .onnx you've
trained). The pretrained options are limited; a brand-new wake word needs a trained model file.

Config edits are line-level regex replacements, so your comments in config.yaml are preserved.

    python -m jarvis.voices list
    python -m jarvis.voices set en-US-AriaNeural
    python -m jarvis.voices wake alexa
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from .config import ROOT

CONFIG_PATH = ROOT / "config.yaml"

# A small curated set so this works offline; edge-tts has hundreds more (see `list`).
CURATED: dict[str, str] = {
    "british male": "en-GB-RyanNeural",
    "british female": "en-GB-SoniaNeural",
    "american male": "en-US-GuyNeural",
    "american female": "en-US-AriaNeural",
    "american female warm": "en-US-JennyNeural",
    "indian male": "en-IN-PrabhatNeural",
    "indian female": "en-IN-NeerjaNeural",
    "australian male": "en-AU-WilliamNeural",
    "australian female": "en-AU-NatashaNeural",
    "irish female": "en-IE-EmilyNeural",
}

# openWakeWord's pretrained models (a custom word needs a trained .onnx file).
BUILTIN_WAKE = ["hey_jarvis", "alexa", "hey_mycroft", "hey_rhasspy", "timer", "weather"]

# descriptor may come after "voice to ..." or before "... voice"
_VOICE_CMD = re.compile(
    r"\b(?:change|switch|set|use|make|give me|i want)\b\s+(?P<pre>.*?)\bvoice\b"
    r"(?:\s+(?:to|like)\s+(?P<post>.+?))?\s*$", re.I)
_RATE_CMD = re.compile(r"\b(speak|talk)\s+(faster|slower|quicker|more slowly)\b|"
                       r"\b(slow down|speed up)\b", re.I)
_WAKE_CMD = re.compile(r"\b(?:change|set|make)\b.*\bwake\s*word\b(?:\s+to)?\s*(?P<word>.+)$", re.I)


def resolve_voice(desc: str) -> str | None:
    """Map a spoken description to an edge-tts voice id."""
    raw = " ".join((desc or "").split()).strip(" .")
    if not raw:
        return None
    # raw voice id, e.g. "en-US-AriaNeural" — keep original case (edge-tts is case-sensitive)
    if re.fullmatch(r"[a-z]{2}-[a-z]{2}-\w+", raw, re.I):
        return raw
    d = raw.lower()
    if d in CURATED:
        return CURATED[d]
    # score curated entries by shared words (gender + accent)
    words = set(re.findall(r"[a-z]+", d))
    best, best_score = None, 0
    for name, vid in CURATED.items():
        score = len(words & set(name.split()))
        if score > best_score:
            best, best_score = vid, score
    if best_score:
        return best
    # bare gender
    if "female" in words or "woman" in words:
        return CURATED["british female"]
    if "male" in words or "man" in words:
        return CURATED["british male"]
    return None


def parse_command(text: str) -> tuple[str, str] | None:
    """Return ('voice', id) / ('wake', model) / ('rate', '+10%'|'-10%') / None for a config change."""
    t = (text or "").strip()
    if m := _WAKE_CMD.search(t):
        word = re.sub(r"[^\w .-]", "", m.group("word")).strip().lower().replace(" ", "_")
        return ("wake", word) if word else None
    if m := _RATE_CMD.search(t):
        faster = bool(re.search(r"faster|quicker|speed up", m.group(0), re.I))
        return ("rate", "+15%" if faster else "-15%")
    if m := _VOICE_CMD.search(t):
        vid = resolve_voice(m.group("post") or m.group("pre") or "")
        return ("voice", vid) if vid else None
    return None


# --------------------------------------------------------------------------- config writing

def _set_scalar(key: str, value: str, quote: bool = False) -> bool:
    try:
        text = CONFIG_PATH.read_text(encoding="utf-8")
    except OSError:
        return False
    val = f'"{value}"' if quote else value
    pat = re.compile(rf"(?m)^(\s*{re.escape(key)}:\s*)(.*?)(\s*#.*)?$")
    if not pat.search(text):
        return False
    new = pat.sub(lambda m: f"{m.group(1)}{val}{m.group(3) or ''}", text, count=1)
    CONFIG_PATH.write_text(new, encoding="utf-8")
    return True


def set_voice(voice_id: str) -> bool:
    return _set_scalar("tts_voice", voice_id)


def set_wake(model: str) -> bool:
    return _set_scalar("wake_model", model)


def bump_rate(delta: str) -> str | None:
    """Adjust tts_rate by delta ('+15%' / '-15%'), clamped to +-80%. Returns the new rate."""
    try:
        text = CONFIG_PATH.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'(?m)^\s*tts_rate:\s*"?([+-]?\d+)%"?', text)
    cur = int(m.group(1)) if m else 0
    new = max(-80, min(80, cur + int(delta.rstrip("%"))))
    return f"+{new}%" if new >= 0 else f"{new}%"


def set_rate(rate: str) -> bool:
    return _set_scalar("tts_rate", rate, quote=True)


# --------------------------------------------------------------------------- CLI

def _list_voices() -> list[str]:
    try:                                   # live full list (needs network)
        import asyncio
        import edge_tts
        voices = asyncio.run(edge_tts.list_voices())
        en = sorted(v["ShortName"] for v in voices if v["ShortName"].startswith("en-"))
        return en or list(CURATED.values())
    except Exception:
        return list(CURATED.values())


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "list"

    if cmd == "list":
        print("Friendly names:")
        for name, vid in CURATED.items():
            print(f"  {name:22} {vid}")
        print("\nAll English edge-tts voices:")
        print("  " + ", ".join(_list_voices()))
        print("\nWake words (built-in):", ", ".join(BUILTIN_WAKE))
        return 0
    if cmd == "set" and len(argv) > 1:
        vid = resolve_voice(" ".join(argv[1:]))
        if not vid:
            print("Unknown voice. Run `list` to see options."); return 1
        print(f"Voice set to {vid}." if set_voice(vid) else "Could not write config.yaml.")
        return 0
    if cmd == "wake" and len(argv) > 1:
        model = argv[1]
        print(f"Wake word set to {model} (restart JARVIS to use it)." if set_wake(model)
              else "Could not write config.yaml.")
        return 0
    print("usage: jarvis.voices [list | set <voice> | wake <model>]")
    return 1


if __name__ == "__main__":
    sys.exit(main())
