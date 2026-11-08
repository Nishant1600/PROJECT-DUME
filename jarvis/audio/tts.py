"""Text-to-speech for JARVIS.

edge-tts (Microsoft's free online neural voices) -> MP3 decoded with miniaudio
-> played with sounddevice. Synthesis runs ahead of playback on its own
thread, and one output stream stays open for a whole burst of sentences, so
consecutive sentences flow without gaps.

    speaker = Speaker()               # en-GB-RyanNeural, British male
    speaker.say("Good evening.")      # returns at once
    speaker.say("All systems online.")
    speaker.stop()                    # barge-in: drop the queue, cut audio now
    speaker.wait()                    # block until everything queued has played
"""

from __future__ import annotations

import asyncio
import collections
import logging
import queue
import re
import threading
import time
import wave

import edge_tts
import miniaudio
import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

DEFAULT_VOICE = "en-GB-RyanNeural"

# --------------------------------------------------------------------------- text prep

_MD_SUBS = [
    (re.compile(r"```[\w+-]*"), " "),                    # code fences
    (re.compile(r"!?\[([^\]]+)\]\([^)]*\)"), r"\1"),     # [text](url) -> text
    (re.compile(r"https?://\S+"), "a link"),             # bare URLs
    (re.compile(r"(?m)^\s{0,3}(?:#{1,6}|>|[-+*•]|\d+[.)])\s+"), ""),  # headings, quotes, bullets
    (re.compile(r"[`*~|]+"), ""),                         # emphasis / inline code / table pipes
    (re.compile(r"\s+"), " "),
]
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_MIN_CHUNK = 24  # merge tiny fragments ("e.g.", "Mr.") into the next sentence


def clean_for_speech(text: str) -> str:
    """Strip markdown that would otherwise be read aloud."""
    for pattern, repl in _MD_SUBS:
        text = pattern.sub(repl, text)
    return text.strip()


def split_sentences(text: str) -> list[str]:
    """Split text into speakable sentences (so long replies start playing sooner)."""
    out: list[str] = []
    carry = ""
    for part in _SENTENCE_END.split(text):
        part = (carry + " " + part).strip() if carry else part.strip()
        if not part:
            continue
        if len(part) < _MIN_CHUNK:
            carry = part
            continue
        out.append(part)
        carry = ""
    if carry:
        if out and len(carry) < 8:
            out[-1] = out[-1] + " " + carry
        else:
            out.append(carry)
    # Only punctuation / symbols left -> nothing to say.
    return [s for s in out if re.search(r"\w", s)]


# --------------------------------------------------------------------------- synthesis

async def _synth_mp3_async(text: str, voice: str, rate: str, volume: str, pitch: str) -> bytes:
    comm = edge_tts.Communicate(text, voice, rate=rate, volume=volume, pitch=pitch)
    buf = bytearray()
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            buf += chunk["data"]
    return bytes(buf)


async def _synth_with_retry(text, voice, rate, volume, pitch, attempts=2) -> bytes:
    for attempt in range(attempts):
        try:
            return await _synth_mp3_async(text, voice, rate, volume, pitch)
        except edge_tts.exceptions.NoAudioReceived:
            return b""  # nothing speakable in the text
        except Exception:
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(0.4)
    return b""


def decode_mp3(data: bytes, sample_rate: int) -> np.ndarray:
    """MP3 bytes -> mono float32 samples in [-1, 1] at ``sample_rate``."""
    decoded = miniaudio.decode(
        data,
        output_format=miniaudio.SampleFormat.FLOAT32,
        nchannels=1,
        sample_rate=sample_rate,
    )
    return np.frombuffer(decoded.samples, dtype=np.float32).copy()


def trim_silence(audio: np.ndarray, sample_rate: int, lead: float = 0.03, tail: float = 0.25,
                 level: float = 0.005) -> np.ndarray:
    """Cut edge-tts's ~0.2 s leading / ~0.9 s trailing silence down to ``lead`` / ``tail`` seconds."""
    loud = np.flatnonzero(np.abs(audio) > level)
    if loud.size == 0:
        return audio[:0]
    start = max(0, loud[0] - int(lead * sample_rate))
    end = min(audio.size, loud[-1] + int(tail * sample_rate))
    return audio[start:end]


def synthesize(text: str, voice: str = DEFAULT_VOICE, rate: str = "+0%",
               sample_rate: int = 24000, volume: str = "+0%", pitch: str = "+0Hz") -> np.ndarray:
    """Blocking one-shot synthesis -> float32 mono samples (needs internet)."""
    mp3 = asyncio.run(_synth_with_retry(text, voice, rate, volume, pitch))
    if not mp3:
        return np.zeros(0, dtype=np.float32)
    return decode_mp3(mp3, sample_rate)


def synthesize_to_wav(text: str, path: str, voice: str = DEFAULT_VOICE, rate: str = "+0%",
                      sample_rate: int = 16000) -> str:
    """Synthesize ``text`` and save it as 16-bit mono PCM WAV. Returns ``path``."""
    audio = synthesize(text, voice=voice, rate=rate, sample_rate=sample_rate)
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return path


def _tone(sample_rate: int, notes=((880.0, 0.07), (1320.0, 0.09)), gain=0.25) -> np.ndarray:
    parts = []
    for freq, dur in notes:
        t = np.arange(int(sample_rate * dur)) / sample_rate
        env = np.minimum(1.0, np.minimum(t, dur - t) / 0.01)  # 10 ms fade in/out
        parts.append(gain * env * np.sin(2 * np.pi * freq * t))
    parts.append(np.zeros(int(sample_rate * 0.05)))
    return np.concatenate(parts).astype(np.float32)


# --------------------------------------------------------------------------- speaker

class _Clip:
    __slots__ = ("audio", "pos")

    def __init__(self, audio: np.ndarray):
        self.audio = audio
        self.pos = 0


_CLOSE = object()


class Speaker:
    """Queued, interruptible text-to-speech.

    ``say()`` never blocks; a synth thread fetches audio from edge-tts and a
    player thread feeds it to one sounddevice output stream that stays open
    while sentences keep coming. ``stop()`` drops everything queued and aborts
    the stream immediately (for barge-in).
    """

    IDLE_CLOSE_SECONDS = 1.5  # close the output device after this long with nothing to play

    def __init__(self, voice: str = DEFAULT_VOICE, rate: str = "+0%", volume: str = "+0%",
                 pitch: str = "+0Hz", device=None):
        self.voice = voice
        self.rate = rate
        self.volume = volume
        self.pitch = pitch
        self.device = device
        self.sample_rate = self._output_rate(device)

        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._gen = 0            # bumped by stop(); items from an older generation are dropped
        self._pending = 0        # clips said but not yet played or dropped
        self._cut = threading.Event()
        self._text_q: queue.Queue = queue.Queue()   # (gen, text)
        self._audio_q: queue.Queue = queue.Queue()  # (gen, samples)
        # Shared with the audio callback: clips being played, and their end times.
        self._feed: collections.deque[_Clip] = collections.deque()
        self._ended: queue.SimpleQueue = queue.SimpleQueue()
        self._closed = False

        threading.Thread(target=self._synth_worker, name="jarvis-tts-synth", daemon=True).start()
        threading.Thread(target=self._play_worker, name="jarvis-tts-play", daemon=True).start()
        log.info("TTS ready: voice=%s rate=%s output=%d Hz", voice, rate, self.sample_rate)

    # ---- public API

    def say(self, text: str) -> None:
        """Queue ``text`` to be spoken; returns immediately."""
        if self._closed or not text:
            return
        parts = split_sentences(clean_for_speech(text))
        if not parts:
            return
        with self._lock:
            gen = self._gen
            self._pending += len(parts)
        for part in parts:
            self._text_q.put((gen, part))

    def chime(self) -> None:
        """Queue a short two-note 'listening' earcon (instant, no network)."""
        if self._closed:
            return
        with self._lock:
            gen = self._gen
            self._pending += 1
        self._audio_q.put((gen, _tone(self.sample_rate)))

    def stop(self) -> None:
        """Clear the queue and cut playback now. Safe to call from any thread."""
        with self._lock:
            self._gen += 1
            self._cut.set()
            dropped = 0
            for q in (self._text_q, self._audio_q):
                while True:
                    try:
                        item = q.get_nowait()
                    except queue.Empty:
                        break
                    if item is _CLOSE:
                        q.put(_CLOSE)
                        break
                    dropped += 1
            self._pending = max(0, self._pending - dropped)
            if self._pending == 0:
                self._idle.notify_all()
            # Wait (briefly) for the player to abort the stream, so the speakers
            # are really silent when we return - matters when the mic opens next.
            if threading.current_thread().name != "jarvis-tts-play":
                self._idle.wait_for(lambda: not self._cut.is_set(), timeout=0.3)

    def wait(self, timeout: float | None = None) -> bool:
        """Block until everything queued has been played (or stopped). False on timeout."""
        # Wait in short slices: an untimed lock wait can't be broken by Ctrl+C on Windows.
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._idle:
            while self._pending > 0:
                remaining = 0.1 if deadline is None else min(0.1, deadline - time.monotonic())
                if remaining <= 0:
                    return False
                self._idle.wait(remaining)
        return True

    @property
    def speaking(self) -> bool:
        """True while anything is queued, being synthesized or playing."""
        return self._pending > 0

    @property
    def playing(self) -> bool:
        """True while audio is actually coming out of the speakers."""
        return bool(self._feed)

    def close(self) -> None:
        self.stop()
        self._closed = True
        self._text_q.put(_CLOSE)
        self._audio_q.put(_CLOSE)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- internals

    @staticmethod
    def _output_rate(device) -> int:
        try:
            return int(sd.query_devices(device, "output")["default_samplerate"])
        except Exception as exc:  # no output device info; edge-tts native rate
            log.warning("Could not query output device (%s); using 24000 Hz", exc)
            return 24000

    def _finish(self, n: int = 1) -> None:
        if n <= 0:
            return
        with self._lock:
            self._pending = max(0, self._pending - n)
            if self._pending == 0:
                self._idle.notify_all()

    def _synth_worker(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            while True:
                item = self._text_q.get()
                if item is _CLOSE:
                    return
                gen, text = item
                if gen != self._gen:
                    self._finish()
                    continue
                audio = None
                try:
                    t0 = time.perf_counter()
                    mp3 = loop.run_until_complete(
                        _synth_with_retry(text, self.voice, self.rate, self.volume, self.pitch))
                    if mp3:
                        audio = trim_silence(decode_mp3(mp3, self.sample_rate), self.sample_rate)
                    log.debug("TTS %.2fs for %r", time.perf_counter() - t0, text[:50])
                except Exception as exc:
                    log.warning("TTS failed for %r: %s: %s", text[:60], type(exc).__name__, exc)
                if audio is None or audio.size == 0 or gen != self._gen:
                    self._finish()
                    continue
                self._audio_q.put((gen, audio))
        finally:
            loop.close()

    def _callback(self, outdata, frames, _time, _status) -> None:
        # Runs on the PortAudio thread: no locks, no allocation beyond slicing.
        out = outdata[:, 0]
        filled = 0
        if not self._cut.is_set():
            feed = self._feed
            while filled < frames and feed:
                clip = feed[0]
                n = min(frames - filled, clip.audio.size - clip.pos)
                out[filled:filled + n] = clip.audio[clip.pos:clip.pos + n]
                clip.pos += n
                filled += n
                if clip.pos >= clip.audio.size:
                    feed.popleft()
                    self._ended.put(time.monotonic())
        if filled < frames:
            out[filled:] = 0

    def _play_worker(self) -> None:
        stream = None
        latency = 0.0
        finishing: collections.deque[float] = collections.deque()  # when handed-off clips leave the speaker
        last_active = time.monotonic()

        def close_stream(abort: bool):
            nonlocal stream
            if stream is not None:
                try:
                    stream.abort() if abort else stream.stop()
                    stream.close()
                except Exception as exc:
                    log.debug("Closing output stream: %s", exc)
                stream = None

        while True:
            # 1. Barge-in: abort the device, drop everything already handed to it.
            if self._cut.is_set():
                close_stream(abort=True)   # after this the callback can't touch the feed
                # Each clip is in exactly one place: still in the feed, ended but not
                # yet collected, or collected and waiting out the output latency.
                n = len(self._feed) + len(finishing)
                self._feed.clear()
                finishing.clear()
                while True:
                    try:
                        self._ended.get_nowait()
                        n += 1
                    except queue.Empty:
                        break
                with self._lock:
                    self._cut.clear()
                    self._idle.notify_all()
                self._finish(n)

            # 2. Clips whose last sample has made it out of the speaker.
            while True:
                try:
                    finishing.append(self._ended.get_nowait() + latency)
                except queue.Empty:
                    break
            now = time.monotonic()
            done = 0
            while finishing and finishing[0] <= now:
                finishing.popleft()
                done += 1
            self._finish(done)

            # 3. Next clip.
            try:
                item = self._audio_q.get(timeout=0.02)
            except queue.Empty:
                item = None
            if item is _CLOSE:
                close_stream(abort=True)
                return
            if item is not None:
                gen, audio = item
                with self._lock:
                    fresh = gen == self._gen and not self._cut.is_set()
                    if fresh:
                        self._feed.append(_Clip(audio))
                if not fresh:
                    self._finish()
                    continue
                if stream is None:
                    try:
                        stream = sd.OutputStream(samplerate=self.sample_rate, channels=1, dtype="float32",
                                                 device=self.device, callback=self._callback)
                        stream.start()
                        latency = float(stream.latency)
                    except Exception as exc:
                        log.warning("Audio output failed: %s: %s", type(exc).__name__, exc)
                        stream = None
                        n = len(self._feed)
                        self._feed.clear()
                        self._finish(n)
                        continue

            # 4. Release the device after a quiet spell.
            if self._feed or finishing or item is not None:
                last_active = time.monotonic()
            elif stream is not None and time.monotonic() - last_active > self.IDLE_CLOSE_SECONDS:
                close_stream(abort=False)
