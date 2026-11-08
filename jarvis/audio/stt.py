"""Speech-to-text for JARVIS.

Records 16 kHz mono from the default microphone with sounddevice, uses the
silero VAD model (the ONNX copy bundled with faster-whisper) to decide when
you have finished speaking, then transcribes with faster-whisper on the GPU.

    listener = Listener()          # loads Whisper once (CUDA float16, else CPU int8)
    text = listener.listen()       # "" if nobody spoke within ~6 s
    listener.transcribe_file("clip.wav")
"""

from __future__ import annotations

import collections
import glob
import logging
import os
import queue
import sys
import threading
import time

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
VAD_FRAME = 512    # 32 ms; the frame size silero expects at 16 kHz
VAD_CONTEXT = 64   # samples of the previous frame silero sees with each frame
FRAME_MS = VAD_FRAME * 1000 / SAMPLE_RATE


# --------------------------------------------------------------------------- CUDA DLLs (Windows)

_CUDA_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn_ops64_9.dll", "cudnn_cnn64_9.dll")


def _add_nvidia_dll_dirs() -> list[str]:
    """Put pip-installed NVIDIA DLLs (nvidia-cublas-cu12, nvidia-cudnn-cu12) on the DLL search path."""
    if sys.platform != "win32":
        return []
    added = []
    for entry in sys.path:
        for bindir in glob.glob(os.path.join(entry, "nvidia", "*", "bin")):
            if bindir in added or not os.path.isdir(bindir):
                continue
            try:
                os.add_dll_directory(bindir)
            except OSError:
                continue
            # cuDNN loads its sub-libraries with plain LoadLibrary, which reads PATH.
            os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")
            added.append(bindir)
    return added


def cuda_problem() -> str | None:
    """None if CTranslate2 can run Whisper on the GPU, else a human-readable reason.

    Checked up front because a missing cuDNN DLL kills the process instead of
    raising an exception that we could fall back from.
    """
    _add_nvidia_dll_dirs()
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() < 1:
            return "no CUDA device visible to CTranslate2"
    except Exception as exc:
        return f"CTranslate2 CUDA check failed: {exc}"
    if sys.platform == "win32":
        import ctypes

        missing = []
        for dll in _CUDA_DLLS:
            try:
                ctypes.WinDLL(dll, winmode=0)  # standard search order, like LoadLibrary
            except OSError:
                missing.append(dll)
        if missing:
            return ("missing " + ", ".join(missing) +
                    " (pip install nvidia-cublas-cu12 \"nvidia-cudnn-cu12==9.*\")")
    return None


# --------------------------------------------------------------------------- VAD

def _silero_path() -> str:
    import faster_whisper.utils

    assets = faster_whisper.utils.get_assets_path()
    found = sorted(glob.glob(os.path.join(assets, "silero_vad*.onnx")))
    if not found:
        raise FileNotFoundError(f"No silero VAD model in {assets}")
    return found[-1]


class SileroStream:
    """Frame-by-frame silero VAD (512 samples at 16 kHz -> speech probability)."""

    def __init__(self, path: str | None = None):
        import onnxruntime

        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 4
        self.session = onnxruntime.InferenceSession(path or _silero_path(), sess_options=opts,
                                                    providers=["CPUExecutionProvider"])
        names = {i.name for i in self.session.get_inputs()}
        self._style = "hc" if {"h", "c"} <= names else "state"
        self.reset()

    def reset(self) -> None:
        self._context = np.zeros(VAD_CONTEXT, dtype=np.float32)
        if self._style == "hc":
            self._h = np.zeros((1, 1, 128), dtype=np.float32)
            self._c = np.zeros((1, 1, 128), dtype=np.float32)
        else:
            self._state = np.zeros((2, 1, 128), dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        x = np.concatenate([self._context, frame.astype(np.float32)])[None, :]
        self._context = x[0, -VAD_CONTEXT:]
        if self._style == "hc":
            prob, self._h, self._c = self.session.run(None, {"input": x, "h": self._h, "c": self._c})
        else:
            prob, self._state = self.session.run(
                None, {"input": x, "state": self._state, "sr": np.array(SAMPLE_RATE, dtype=np.int64)})
        return float(np.asarray(prob).reshape(-1)[0])


# --------------------------------------------------------------------------- files

def load_audio(path: str) -> np.ndarray:
    """Decode an audio file to 16 kHz mono float32.

    Uses miniaudio (WAV, MP3, FLAC, Vorbis). faster-whisper's own decoder
    (PyAV) is only a fallback: faster-whisper 1.2.1 passes an argument that
    PyAV 19 removed, so it fails on current installs.
    """
    import miniaudio

    try:
        decoded = miniaudio.decode_file(path, output_format=miniaudio.SampleFormat.FLOAT32,
                                        nchannels=1, sample_rate=SAMPLE_RATE)
        return np.frombuffer(decoded.samples, dtype=np.float32).copy()
    except miniaudio.DecodeError:
        from faster_whisper.audio import decode_audio

        return decode_audio(path, sampling_rate=SAMPLE_RATE)


# --------------------------------------------------------------------------- mic

def _open_mic(device, blocksize: int, on_block):
    """16 kHz mono float32 input stream; resamples if the device refuses 16 kHz."""
    def cb16(indata, _frames, _time, _status):
        on_block(indata[:, 0].copy())

    try:
        return sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                              blocksize=blocksize, device=device, callback=cb16)
    except Exception as exc:
        native = int(sd.query_devices(device, "input")["default_samplerate"])
        log.info("Mic refused %d Hz (%s); recording at %d Hz and resampling", SAMPLE_RATE, exc, native)
        ratio = SAMPLE_RATE / native

        def cbn(indata, frames, _time, _status):
            n_out = max(1, int(round(frames * ratio)))
            on_block(np.interp(np.linspace(0, frames - 1, n_out), np.arange(frames),
                               indata[:, 0]).astype(np.float32))

        return sd.InputStream(samplerate=native, channels=1, dtype="float32",
                              blocksize=int(round(blocksize / ratio)), device=device, callback=cbn)


# --------------------------------------------------------------------------- listener

class Listener:
    """Microphone -> end-of-speech detection -> Whisper transcript."""

    def __init__(self, model_size: str = "small", device: str = "cuda", compute_type: str = "float16",
                 language: str | None = None, mic_device=None, vad_threshold: float = 0.5,
                 beam_size: int = 5, initial_prompt: str | None = None, download_root: str | None = None):
        self.model_size = model_size
        self.language = language
        self.mic_device = mic_device
        self.vad_threshold = vad_threshold
        self.beam_size = beam_size
        self.initial_prompt = initial_prompt
        self.last_audio: np.ndarray | None = None
        self.last_info = None
        self._cancel = threading.Event()
        self._model_lock = threading.Lock()   # captions and the final transcript share the GPU model

        t0 = time.perf_counter()
        self.model, self.device, self.compute_type = self._load(model_size, device, compute_type,
                                                                download_root)
        self.vad = SileroStream()
        log.info("STT ready: whisper-%s on %s (%s) in %.1fs", model_size, self.device,
                 self.compute_type, time.perf_counter() - t0)

    # ---- model loading

    @staticmethod
    def _load(model_size, device, compute_type, download_root):
        from faster_whisper import WhisperModel

        attempts = []
        if device != "cpu":
            problem = cuda_problem() if device.startswith("cuda") else None
            if problem:
                log.warning("Whisper: not using %s: %s", device, problem)
            else:
                attempts.append((device, compute_type))
        attempts.append(("cpu", "int8" if device != "cpu" else compute_type))

        last_exc = None
        for dev, ctype in attempts:
            try:
                model = WhisperModel(model_size, device=dev, compute_type=ctype,
                                     download_root=download_root)
                # Warm-up: runs encoder + decoder once, so cuBLAS/cuDNN problems
                # show up here (and the first real request is fast).
                noise = (np.random.default_rng(0).standard_normal(SAMPLE_RATE) * 0.01).astype(np.float32)
                list(model.transcribe(noise, language="en", beam_size=1, without_timestamps=True)[0])
                log.info("Whisper using device=%s compute_type=%s", dev, ctype)
                return model, dev, ctype
            except Exception as exc:
                last_exc = exc
                log.warning("Whisper on %s/%s failed (%s: %s)%s", dev, ctype, type(exc).__name__, exc,
                            "; falling back to CPU int8" if dev != "cpu" else "")
        raise RuntimeError(f"Could not load Whisper model {model_size!r}: {last_exc}")

    # ---- public API

    def listen(self, max_seconds: float = 60, silence_ms: int = 1200, start_timeout: float = 6.0,
               max_silence_ms: int = 2800, on_level=None, on_speech_start=None,
               on_partial=None) -> str:
        """Record one utterance and return its transcript ('' if nobody spoke).

        End of turn is decided like a person would: a pause of ``silence_ms`` ends the turn only if
        what was said so far sounds finished; if it trails off ("and...", "so the...") JARVIS keeps
        listening up to ``max_silence_ms`` of silence.
        """
        audio = self.record(max_seconds=max_seconds, silence_ms=silence_ms,
                            start_timeout=start_timeout, max_silence_ms=max_silence_ms,
                            on_level=on_level, on_speech_start=on_speech_start,
                            on_partial=on_partial)
        if audio is None:
            return ""
        return self.transcribe(audio)

    def cancel(self) -> None:
        """Abort a listen() in progress from another thread (it returns '')."""
        self._cancel.set()

    def record(self, max_seconds: float = 60, silence_ms: int = 1200, start_timeout: float = 6.0,
               max_silence_ms: int = 2800, on_level=None, on_speech_start=None,
               on_partial=None) -> np.ndarray | None:
        """Record until end of turn. Returns float32 16 kHz audio, or None if nobody spoke.

        ``max_seconds`` caps the utterance itself (counted from the first word);
        ``start_timeout`` is how long to wait for the first word.
        Callbacks (all optional, called from this thread or a worker thread):
          on_level(rms 0..1) ~31x per second, on_speech_start(), on_partial(text) while talking.
        """
        self._cancel.clear()
        self.vad.reset()
        blocks: queue.Queue = queue.Queue()
        thr, neg_thr = self.vad_threshold, max(0.05, self.vad_threshold - 0.15)
        onset_frames = 3                                  # ~96 ms of speech to count as speaking
        end_frames = max(1, int(silence_ms / FRAME_MS))
        hard_end_frames = max(end_frames, int(max_silence_ms / FRAME_MS))
        pre_roll = collections.deque(maxlen=int(400 / FRAME_MS))  # keep the first syllable
        tail_keep = int(300 / FRAME_MS)

        captured: list[np.ndarray] = []
        started = False
        speech_run = silence_run = 0
        last_speech_idx = 0
        checked_at = -1          # last_speech_idx when completeness was last judged
        peak = 0.0
        pending = np.zeros(0, dtype=np.float32)
        partials = _PartialTranscriber(self, on_partial) if on_partial else None

        t_open = time.monotonic()
        try:
            with _open_mic(self.mic_device, VAD_FRAME, blocks.put):
                t_start = time.monotonic()
                log.debug("Mic opened in %.0f ms", (t_start - t_open) * 1000)
                done = False
                while not done:
                    if self._cancel.is_set():
                        return None
                    try:
                        block = blocks.get(timeout=0.5)
                    except queue.Empty:
                        block = None
                    if block is not None:
                        pending = np.concatenate([pending, block])
                    while pending.size >= VAD_FRAME:
                        frame, pending = pending[:VAD_FRAME], pending[VAD_FRAME:]
                        fpeak = float(np.abs(frame).max())
                        peak = max(peak, fpeak)
                        if on_level:
                            on_level(min(1.0, float(np.sqrt(np.mean(frame * frame))) * 6))
                        prob = self.vad(frame)
                        if not started:
                            pre_roll.append(frame)
                            speech_run = speech_run + 1 if prob >= thr else 0
                            if speech_run >= onset_frames:
                                started = True
                                t_speech = time.monotonic()
                                captured.extend(pre_roll)
                                last_speech_idx = len(captured)
                                log.debug("Speech started after %.1fs", t_speech - t_start)
                                if on_speech_start:
                                    on_speech_start()
                            continue
                        captured.append(frame)
                        if prob >= thr:
                            silence_run = 0
                            last_speech_idx = len(captured)
                            if partials:
                                partials.maybe_submit(captured, last_speech_idx)
                        elif prob < neg_thr:
                            silence_run += 1
                        if silence_run >= hard_end_frames:
                            done = True
                            break
                        if silence_run >= end_frames and checked_at != last_speech_idx:
                            # A pause: did the sentence sound finished?
                            checked_at = last_speech_idx
                            so_far = self._quick(np.concatenate(captured[:last_speech_idx]))
                            if not looks_unfinished(so_far):
                                done = True
                                break
                            log.debug("Pause after unfinished phrase %r; still listening", so_far[-40:])
                    else:
                        now = time.monotonic()
                        if not started and now - t_start >= start_timeout:
                            if peak == 0.0:
                                log.warning("Microphone delivered pure silence. Check Windows Settings > "
                                            "Privacy & security > Microphone > 'Let desktop apps access "
                                            "your microphone', and that the right input device is default.")
                            else:
                                log.info("Nobody spoke within %.0fs", start_timeout)
                            return None
                        if started and now - t_speech >= max_seconds:
                            log.info("Utterance hit the %.0fs cap", max_seconds)
                            done = True
        finally:
            if partials:
                partials.close()

        audio = np.concatenate(captured[: last_speech_idx + tail_keep])
        self.last_audio = audio
        log.debug("Captured %.1fs of speech", audio.size / SAMPLE_RATE)
        return audio

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe float32 16 kHz mono audio."""
        return self._run(np.asarray(audio, dtype=np.float32))

    def transcribe_file(self, path: str) -> str:
        """Transcribe an audio file (WAV/MP3/FLAC/OGG; others via PyAV)."""
        return self._run(load_audio(path))

    # ---- internals

    def _quick(self, audio: np.ndarray) -> str:
        """Fast greedy transcript, used for live captions and end-of-turn checks."""
        return self._run(np.asarray(audio, dtype=np.float32), beam_size=1, quiet=True)

    def _run(self, audio, beam_size: int | None = None, quiet: bool = False) -> str:
        t0 = time.perf_counter()
        with self._model_lock:
            segments, info = self.model.transcribe(
                audio,
                language=self.language,
                beam_size=beam_size or self.beam_size,
                vad_filter=False,
                condition_on_previous_text=False,
                without_timestamps=True,
                initial_prompt=self.initial_prompt,
            )
            parts = []
            for seg in segments:
                # Whisper invents text ("Thank you.") on noise; drop segments it doubts itself.
                if seg.no_speech_prob > 0.6 and seg.avg_logprob < -1.0:
                    continue
                parts.append(seg.text.strip())
        text = " ".join(p for p in parts if p).strip()
        if info.duration < 2.0 and text.lower().strip(" .!") in _PHANTOMS:
            text = ""
        if not quiet:
            self.last_info = info
            log.info("Transcribed %.1fs audio in %.2fs [%s %.0f%%]: %r", info.duration,
                     time.perf_counter() - t0, info.language, info.language_probability * 100, text)
        return text


# --------------------------------------------------------------------------- turn-taking helpers

# Whisper's favourite inventions on near-silence.
_PHANTOMS = {"thank you", "thanks for watching", "thank you for watching", "you", "uh", "um", "hmm"}

# A phrase ending in one of these is almost never a finished request.
_CONTINUERS = set("""
and or but so because then than the a an to of for with in on at from by about into onto my your
our their his her its this that these those which who whom whose if when while where what how why
um uh er like also is are was were be been am i i'm we you it's can could would should will shall
may might must do does did not just very really plus please let me
""".split())


def looks_unfinished(text: str) -> bool:
    """True if a transcript sounds like the speaker paused mid-thought."""
    t = text.strip()
    if not t:
        return True
    if t.endswith(("...", "…", ",", "-", "—", ":")):
        return True
    last = t.rstrip(".?!").split()[-1].lower().strip("\"'()") if t.rstrip(".?!").split() else ""
    if last in _CONTINUERS:
        return True
    if t.endswith((".", "?", "!")):
        return False
    # No closing punctuation: Whisper usually adds it to complete sentences.
    return len(t.split()) >= 3


class _PartialTranscriber:
    """Live captions: re-transcribes the utterance so far on a worker thread, never blocking the mic."""

    def __init__(self, listener: "Listener", on_partial):
        self.listener = listener
        self.on_partial = on_partial
        self._busy = threading.Event()
        self._last_submit = 0.0
        self._interval = 0.8
        self._closed = False

    def maybe_submit(self, captured: list, upto: int) -> None:
        now = time.monotonic()
        if self._busy.is_set() or now - self._last_submit < self._interval or upto < 10:
            return
        self._last_submit = now
        self._busy.set()
        audio = np.concatenate(captured[:upto])
        threading.Thread(target=self._work, args=(audio,), daemon=True).start()

    def _work(self, audio: np.ndarray) -> None:
        try:
            t0 = time.perf_counter()
            text = self.listener._quick(audio)
            # Back off on long utterances so captions never hog the GPU.
            self._interval = max(0.8, (time.perf_counter() - t0) * 2.5)
            if text and not self._closed:
                self.on_partial(text)
        except Exception:
            log.debug("partial transcription failed", exc_info=True)
        finally:
            self._busy.clear()

    def close(self) -> None:
        self._closed = True
