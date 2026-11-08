"""Voice-layer demo and self-test.

    .venv\\Scripts\\python -m jarvis.audio.demo              # live: wake word / hotkey -> listen -> "You said: ..."
    .venv\\Scripts\\python -m jarvis.audio.demo --selftest   # non-interactive checks
    .venv\\Scripts\\python -m jarvis.audio.demo --selftest --no-play   # ...without using the speakers
"""

from __future__ import annotations

import argparse
import difflib
import logging
import os
import re
import sys
import tempfile
import time

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

SELFTEST_SENTENCE = "Good evening sir, all systems are online and ready for your command."


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _wer(ref: list[str], hyp: list[str]) -> float:
    """Word error rate (Levenshtein over words)."""
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
    return d[-1] / max(1, len(ref))


class _Results:
    def __init__(self):
        self.rows: list[tuple[str, bool, str, bool]] = []

    def add(self, name: str, ok: bool, detail: str = "", required: bool = True):
        self.rows.append((name, ok, detail, required))
        tag = "PASS" if ok else ("FAIL" if required else "WARN")
        print(f"  [{tag}] {name}: {detail}", flush=True)

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _, req in self.rows if req)


# --------------------------------------------------------------------------- self-test

def selftest(args) -> int:
    import numpy as np

    from jarvis.audio import stt, tts, wake

    res = _Results()
    tmpdir = tempfile.mkdtemp(prefix="jarvis_selftest_")
    wav = os.path.join(tmpdir, "sentence.wav")
    print(f"JARVIS voice self-test (temp files in {tmpdir})\n", flush=True)

    # 1. TTS synthesis -> WAV
    print("1) edge-tts synthesis", flush=True)
    try:
        t0 = time.perf_counter()
        tts.synthesize_to_wav(SELFTEST_SENTENCE, wav, voice=args.voice, rate=args.rate, sample_rate=16000)
        import wave

        with wave.open(wav) as w:
            dur = w.getnframes() / w.getframerate()
        res.add("synthesize to WAV", dur > 1.0,
                f"{dur:.1f}s of audio in {time.perf_counter() - t0:.2f}s ({args.voice})")
    except Exception as exc:
        res.add("synthesize to WAV", False, f"{type(exc).__name__}: {exc} (needs internet)")
        return _summary(res)

    # 2. Whisper transcription of that WAV
    print("\n2) faster-whisper transcription", flush=True)
    t0 = time.perf_counter()
    listener = stt.Listener(model_size=args.model, device=args.device, compute_type=args.compute_type,
                            language=args.language)
    res.add("load Whisper", True, f"{args.model} on {listener.device} ({listener.compute_type}) "
            f"in {time.perf_counter() - t0:.1f}s", required=True)
    if args.device.startswith("cuda"):
        res.add("CUDA used", listener.device.startswith("cuda"),
                listener.device if listener.device.startswith("cuda") else
                f"fell back to CPU: {stt.cuda_problem() or 'see warnings above'}", required=False)
    t0 = time.perf_counter()
    heard = listener.transcribe_file(wav)
    wer = _wer(_words(SELFTEST_SENTENCE), _words(heard))
    res.add("transcribe_file words match", wer <= 0.1,
            f"WER {wer:.0%} in {time.perf_counter() - t0:.2f}s -> {heard!r}")

    # 3. VAD on the same audio (the end-of-speech detector listen() uses)
    print("\n3) silero VAD", flush=True)
    audio = stt.load_audio(wav)
    vad = stt.SileroStream()
    frames = [audio[i:i + stt.VAD_FRAME] for i in range(0, len(audio) - stt.VAD_FRAME + 1, stt.VAD_FRAME)]
    probs = np.array([vad(f) for f in frames])
    vad.reset()
    quiet = max(vad(np.zeros(stt.VAD_FRAME, np.float32)) for _ in range(30))
    speech_share = float((probs >= 0.5).mean())
    res.add("VAD detects speech", speech_share > 0.4 and quiet < 0.5,
            f"{speech_share:.0%} of speech frames above 0.5, silence max {quiet:.2f}")

    # 4. Wake-word model
    print("\n4) openWakeWord", flush=True)
    try:
        t0 = time.perf_counter()
        ww = wake.WakeWord(model=args.wake_model, threshold=args.threshold, hotkey=None)
        silence_score = ww.score_clip(np.zeros(16000 * 2, dtype=np.int16))
        res.add("wake model loads", silence_score < args.threshold,
                f"{ww.model_name} loaded in {time.perf_counter() - t0:.1f}s, silence score {silence_score:.3f}")
        # Bonus: does a synthetic "Hey Jarvis" trigger it? (Real voices are what matter.)
        try:
            best = 0.0
            for voice in (args.voice, "en-US-GuyNeural", "en-US-JennyNeural"):
                clip = tts.synthesize("Hey Jarvis!", voice=voice, sample_rate=16000)
                best = max(best, ww.score_clip(clip))
            res.add("synthetic 'Hey Jarvis' triggers", best >= args.threshold,
                    f"best score {best:.2f} (threshold {args.threshold})", required=False)
        except Exception as exc:
            res.add("synthetic 'Hey Jarvis' triggers", False, str(exc), required=False)
    except Exception as exc:
        res.add("wake model loads", False, f"{type(exc).__name__}: {exc}")

    # 5. Playback + barge-in
    if args.no_play:
        print("\n5) speaker playback: skipped (--no-play)", flush=True)
    else:
        print("\n5) speaker playback and stop()", flush=True)
        try:
            speaker = tts.Speaker(voice=args.voice, rate=args.rate)
            speaker.say("This is a long sentence that the self-test will interrupt part of the way "
                        "through, to prove that barge-in cuts the audio straight away.")
            t0 = time.monotonic()
            while speaker.speaking and time.monotonic() - t0 < 10 and not speaker.playing:
                time.sleep(0.02)
            started = speaker.playing
            time.sleep(1.0)
            t1 = time.perf_counter()
            speaker.stop()
            stopped = speaker.wait(timeout=1.0)
            cut_ms = (time.perf_counter() - t1) * 1000
            res.add("stop() cuts playback", started and stopped and not speaker.speaking and cut_ms < 500,
                    f"playing={started}, silent {cut_ms:.0f} ms after stop()")
            t0 = time.perf_counter()
            speaker.say("Voice self-test complete.")
            speaker.say("Second sentence, queued behind the first.")
            done = speaker.wait(timeout=20)
            res.add("say() + wait()", done, f"two sentences played in {time.perf_counter() - t0:.1f}s")
            speaker.close()
        except Exception as exc:
            res.add("speaker playback", False, f"{type(exc).__name__}: {exc}")

    # 6. Microphone (informational: Windows privacy settings can block it)
    print("\n6) microphone", flush=True)
    try:
        import sounddevice as sd

        rec = sd.rec(int(0.6 * 16000), samplerate=16000, channels=1, dtype="float32")
        sd.wait()
        peak = float(np.abs(rec).max())
        name = sd.query_devices(kind="input")["name"]
        res.add("mic delivers audio", peak > 0.0,
                f"{name}: peak {peak:.4f}" + ("" if peak > 0 else
                " - all zeros: allow mic access for desktop apps in Windows privacy settings"),
                required=False)
    except Exception as exc:
        res.add("mic delivers audio", False, f"{type(exc).__name__}: {exc}", required=False)

    return _summary(res)


def _summary(res: _Results) -> int:
    print("\nSELF-TEST " + ("PASSED" if res.ok else "FAILED"), flush=True)
    return 0 if res.ok else 1


# --------------------------------------------------------------------------- live loop

def live(args) -> int:
    from jarvis.audio.stt import Listener
    from jarvis.audio.tts import Speaker
    from jarvis.audio.wake import WakeWord

    speaker = Speaker(voice=args.voice, rate=args.rate)
    listener = Listener(model_size=args.model, device=args.device, compute_type=args.compute_type,
                        language=args.language)
    wake = WakeWord(model=args.wake_model, threshold=args.threshold, hotkey=args.hotkey or None)
    speaker.say("Voice systems online.")
    speaker.wait()
    hint = f"say 'hey jarvis'" + (f" or press {args.hotkey}" if args.hotkey else "")
    try:
        while True:
            print(f"\n[waiting] {hint}   (Ctrl+C to quit)", flush=True)
            source = wake.wait()
            speaker.stop()
            print(f"[{source}] listening...", flush=True)
            speaker.chime()
            speaker.wait()
            t0 = time.perf_counter()
            text = listener.listen()
            if not text:
                print("[heard nothing]", flush=True)
                speaker.say("I didn't catch that.")
                speaker.wait()
                continue
            print(f"[you] {text}   ({time.perf_counter() - t0:.1f}s incl. speaking)", flush=True)
            wake.start_background(speaker.stop)   # say "hey jarvis" / hotkey again to interrupt
            speaker.say(f"You said: {text}")
            speaker.wait()
            wake.stop_background()
    except KeyboardInterrupt:
        print("\nbye", flush=True)
    finally:
        wake.close()
        speaker.close()
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m jarvis.audio.demo", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--selftest", action="store_true", help="run the non-interactive self-test")
    p.add_argument("--no-play", action="store_true", help="self-test: don't use the speakers")
    p.add_argument("--voice", default="en-GB-RyanNeural")
    p.add_argument("--rate", default="+0%")
    p.add_argument("--model", default="small", help="Whisper size: tiny/base/small/medium/large-v3")
    p.add_argument("--device", default="cuda")
    p.add_argument("--compute-type", default="float16")
    p.add_argument("--language", default=None, help="e.g. en (default: auto-detect)")
    p.add_argument("--wake-model", default="hey_jarvis")
    p.add_argument("--threshold", type=float, default=0.5)
    p.add_argument("--hotkey", default="ctrl+alt+space", help="push-to-talk hotkey ('' to disable)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("faster_whisper", "httpx", "httpx2", "httpcore", "urllib3", "huggingface_hub",
                  "websockets", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    return selftest(args) if args.selftest else live(args)


if __name__ == "__main__":
    sys.exit(main())
