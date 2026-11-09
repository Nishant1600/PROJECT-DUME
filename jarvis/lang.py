"""Multilingual mode (#63): switch listening and speaking to another language.

"Talk to me in Hindi" / "switch to English" flips both halves of the pipeline: the
speech-to-text language hint (so Whisper transcribes the right language) and the
text-to-speech voice (so replies are spoken naturally). The change is applied to the
live speaker/listener and saved to config.yaml (comments preserved, via voices._set_scalar).

    python -m jarvis.lang set hindi
    python -m jarvis.lang list
"""
from __future__ import annotations

import re
import sys

# language name/alias -> (whisper code, a natural edge-tts voice)
LANGS: dict[str, tuple[str, str]] = {
    "english": ("en", "en-GB-RyanNeural"),
    "hindi": ("hi", "hi-IN-MadhurNeural"),
    "hinglish": ("hi", "hi-IN-MadhurNeural"),      # Hindi voice handles mixed well
    "spanish": ("es", "es-ES-AlvaroNeural"),
    "french": ("fr", "fr-FR-HenriNeural"),
    "german": ("de", "de-DE-ConradNeural"),
    "japanese": ("ja", "ja-JP-KeitaNeural"),
    "mandarin": ("zh", "zh-CN-YunxiNeural"),
    "chinese": ("zh", "zh-CN-YunxiNeural"),
    "arabic": ("ar", "ar-SA-HamedNeural"),
    "tamil": ("ta", "ta-IN-ValluvarNeural"),
    "telugu": ("te", "te-IN-MohanNeural"),
    "bengali": ("bn", "bn-IN-BashkarNeural"),
    "marathi": ("mr", "mr-IN-ManoharNeural"),
}
_ALIASES = {"hindustani": "hindi", "espanol": "spanish", "español": "spanish",
            "francais": "french", "français": "french", "deutsch": "german",
            "putonghua": "mandarin", "en": "english", "hi": "hindi"}

_WORD = r"[a-zÀ-ɏ]+"


def resolve(name: str) -> str | None:
    n = (name or "").strip().lower()
    n = _ALIASES.get(n, n)
    return n if n in LANGS else None


def parse_command(text: str) -> tuple[str, str, str] | None:
    """Return (lang_name, whisper_code, tts_voice) if the text asks to switch language, else None.

    Conservative: only fires on an explicit directive — "... in/to/into <lang>",
    "switch/change (to) <lang>", or "<lang> please" — so "I can speak Hindi" isn't hijacked.
    """
    t = (text or "").strip().lower()
    if not t:
        return None
    candidates: list[str] = []
    candidates += re.findall(r"\b(?:in|to|into)\s+(" + _WORD + r")", t)
    m = re.search(r"\b(?:switch|change)\s+(?:the\s+)?(?:language\s+)?(?:to\s+)?(" + _WORD + r")", t)
    if m:
        candidates.append(m.group(1))
    m = re.search(r"\b(" + _WORD + r")\s+please\b", t)
    if m:
        candidates.append(m.group(1))
    for c in candidates:
        lang = resolve(c)
        if lang:
            code, voice = LANGS[lang]
            return lang, code, voice
    return None


def set_language(code: str, voice: str) -> bool:
    """Persist the language hint and voice to config.yaml."""
    from .voices import _set_scalar
    ok1 = _set_scalar("language", code)
    ok2 = _set_scalar("tts_voice", voice)
    return ok1 and ok2


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "list"
    if cmd == "set" and len(argv) > 1:
        lang = resolve(" ".join(argv[1:]))
        if not lang:
            print("Unknown language. Run `list` to see options."); return 1
        code, voice = LANGS[lang]
        ok = set_language(code, voice)
        print(f"Language set to {lang} (whisper={code}, voice={voice})." if ok
              else "Could not write config.yaml.")
        return 0 if ok else 1
    print("Languages:")
    for name, (code, voice) in LANGS.items():
        print(f"  {name:10} {code:3} {voice}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
