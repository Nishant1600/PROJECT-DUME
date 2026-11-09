"""Train your own wake word (#68): a guided openWakeWord flow.

Teaching JARVIS a brand-new trigger phrase ("hey friday") needs a trained openWakeWord
model. Real training is heavy, so this module does the achievable, local part: it plans
the job, synthesises positive samples of your phrase in several voices with edge-tts (and
can record a few of your own), and either kicks off training when the training extras are
installed, or hands you a clear step-by-step guide plus the dataset to run the official
trainer. When a model is produced, point voice.wake_model at its .onnx and restart.

    python -m jarvis.waketrain plan "hey friday"
    python -m jarvis.waketrain synth "hey friday"      # generate TTS positives
    python -m jarvis.waketrain guide "hey friday"
"""
from __future__ import annotations

import argparse
import re
import sys

from .config import RUNTIME_DIR

WAKE_DIR = RUNTIME_DIR / "wake"

# A spread of voices so the positive set isn't one timbre (edge-tts short names).
SYNTH_VOICES = ["en-GB-RyanNeural", "en-GB-SoniaNeural", "en-US-GuyNeural",
                "en-US-AriaNeural", "en-IN-PrabhatNeural", "en-IN-NeerjaNeural",
                "en-AU-WilliamNeural", "en-AU-NatashaNeural"]

_TRIGGER = re.compile(
    r"\b(train|teach you|create|make|add|build)\b.*\b(?:a |my |new |custom )?wake\s*word\b"
    r"|\bcustom wake\s*word\b|\bnew wake\s*word\b", re.I)


def is_trigger(text: str) -> bool:
    return bool(_TRIGGER.search((text or "").strip()))


def slug(phrase: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (phrase or "").lower()).strip("_") or "wake"


def plan(phrase: str, positives: int = 40) -> dict:
    """A structured training plan for a phrase (pure — no I/O)."""
    s = slug(phrase)
    return {
        "phrase": phrase.strip(),
        "slug": s,
        "model_file": f"{s}.onnx",
        "dataset_dir": str(WAKE_DIR / s),
        "synth_voices": SYNTH_VOICES,
        "target_positive_samples": positives,
        "can_train_here": can_train(),
        "steps": [
            f"Generate ~{positives} spoken samples of \"{phrase.strip()}\" (several voices via edge-tts).",
            "Record 5-10 samples in your own voice for robustness (optional but recommended).",
            "Mix in openWakeWord's standard negative/background set.",
            "Train an openWakeWord model (the training extras do this; see the guide if not installed).",
            f"Save {s}.onnx, set voice.wake_model to its path in config.yaml, and restart JARVIS.",
        ],
    }


def can_train() -> bool:
    """Whether the openWakeWord training pipeline is importable here."""
    import importlib.util
    try:
        return importlib.util.find_spec("openwakeword.train") is not None
    except (ImportError, ValueError):
        return False


def synth_samples(phrase: str, voices: list[str] | None = None, cfg: dict | None = None) -> dict:
    """Best-effort: write TTS positive clips of the phrase. Returns {written, dir, note}."""
    voices = voices or SYNTH_VOICES
    s = slug(phrase)
    out = WAKE_DIR / s / "positives"
    try:
        import asyncio
        import edge_tts
    except Exception:
        return {"written": 0, "dir": str(out),
                "note": "edge-tts not installed — can't synthesise samples here."}
    out.mkdir(parents=True, exist_ok=True)

    async def _one(voice: str, idx: int):
        path = out / f"{s}_{voice}_{idx}.mp3"
        await edge_tts.Communicate(phrase, voice).save(str(path))

    written = 0
    try:
        for i, v in enumerate(voices):
            asyncio.run(_one(v, i))
            written += 1
    except Exception as e:
        return {"written": written, "dir": str(out), "note": f"stopped: {str(e)[:80]}"}
    return {"written": written, "dir": str(out), "note": "ok"}


def guide(phrase: str) -> str:
    p = plan(phrase)
    here = ("I can train it here — the openWakeWord training extras are installed."
            if p["can_train_here"] else
            "The training extras aren't installed here, so after I prepare the dataset you'll run "
            "the official openWakeWord trainer (`pip install openwakeword[train]`, then its training "
            "script) to produce the .onnx.")
    lines = [f"To teach me the wake word \"{p['phrase']}\":", ""]
    lines += [f"{i+1}. {step}" for i, step in enumerate(p["steps"])]
    lines += ["", here,
              f"Dataset will be prepared under: {p['dataset_dir']}",
              f"Final model: {p['model_file']} -> set voice.wake_model to it and restart."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jarvis.waketrain", description="Guided custom wake-word training")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("plan", "synth", "guide"):
        sp = sub.add_parser(name)
        sp.add_argument("phrase", nargs="+")
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help(); return 1
    phrase = " ".join(args.phrase)
    if args.cmd == "plan":
        import json
        print(json.dumps(plan(phrase), indent=2))
    elif args.cmd == "synth":
        res = synth_samples(phrase)
        print(f"Wrote {res['written']} sample(s) to {res['dir']} ({res['note']}).")
    else:
        print(guide(phrase))
    return 0


if __name__ == "__main__":
    sys.exit(main())
