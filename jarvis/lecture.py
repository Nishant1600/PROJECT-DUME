"""Lecture / meeting capture for JARVIS (feature #33).

Turns a recording into a timestamped transcript using the same faster-whisper model
JARVIS already loads for the microphone - so this adds no new dependency. The brain
then reads the transcript and produces a summary and action items.

    python -m jarvis.lecture transcribe lecture.mp3
    python -m jarvis.lecture transcribe talk.wav --out notes/talk.txt --model medium

Loading the model is lazy (only when transcribing) and can take a while on first run.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_config, resolve_path


def _hms(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def transcribe(audio: str, out: str | None = None, model_size: str | None = None,
               device: str | None = None, compute_type: str | None = None,
               language: str | None = None) -> dict:
    path = resolve_path(audio)
    if not path.exists():
        return {"ok": False, "error": f"No such audio file: {path}"}

    cfg = load_config()["voice"]
    model_size = model_size or cfg["whisper_model"]
    device = device or cfg["whisper_device"]
    compute_type = compute_type or cfg["whisper_compute_type"]
    language = language if language is not None else cfg.get("language")

    from .audio.stt import Listener   # reuses the CUDA-DLL handling + CPU fallback
    model, dev, ctype = Listener._load(model_size, device, compute_type, None)

    segments, info = model.transcribe(str(path), language=language, beam_size=5, vad_filter=True)
    lines, plain = [], []
    for seg in segments:
        text = seg.text.strip()
        if text:
            lines.append(f"[{_hms(seg.start)}] {text}")
            plain.append(text)

    out_file = resolve_path(out) if out else resolve_path("workspace") / (path.stem + ".transcript.txt")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    header = (f"# Transcript of {path.name}\n"
              f"# {_hms(info.duration)} · language {info.language} · model {model_size} ({dev})\n\n")
    out_file.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return {"ok": True, "audio": str(path), "out": str(out_file), "device": dev,
            "duration": _hms(info.duration), "language": info.language,
            "segments": len(lines), "words": len(" ".join(plain).split())}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jarvis.lecture", description="Transcribe a recording")
    sub = ap.add_subparsers(dest="cmd", required=True)
    tp = sub.add_parser("transcribe", help="transcribe an audio file to a timestamped transcript")
    tp.add_argument("audio")
    tp.add_argument("--out", default=None)
    tp.add_argument("--model", default=None)
    tp.add_argument("--device", default=None)
    tp.add_argument("--language", default=None)
    args = ap.parse_args(argv)

    if args.cmd == "transcribe":
        res = transcribe(args.audio, out=args.out, model_size=args.model,
                         device=args.device, language=args.language)
        if not res.get("ok"):
            print(res["error"], file=sys.stderr)
            return 1
        print(f"Transcribed {res['duration']} of audio ({res['language']}, {res['words']} words, "
              f"{res['segments']} segments) on {res['device']}.")
        print(f"Transcript: {res['out']}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
