"""Wake word for JARVIS: openWakeWord's pretrained "hey jarvis" model, plus a
push-to-talk hotkey (``keyboard`` package) that triggers the same way.

    wake = WakeWord()                      # downloads the ONNX models on first use
    wake.wait()                            # blocks until "hey jarvis" or ctrl+alt+space
    wake.start_background(speaker.stop)    # while speaking: say it again to interrupt
    wake.stop_background()

The microphone is opened only while someone is waiting (``wait()`` or a
background callback), so it is free for ``Listener.listen()`` otherwise.
"""

from __future__ import annotations

import hashlib
import itertools
import logging
import os
import queue
import threading
import time
import urllib.request

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME = 1280  # 80 ms: the chunk size openWakeWord is built around

# Official openWakeWord v0.5.1 release assets, pinned by SHA-256. GitHub's
# release CDN sometimes answers 504, so there are three routes to the same
# bytes: the release URL, the GitHub API asset URL, and a Hugging Face mirror
# (accepted only when the hash matches the official file).
_RELEASE_URL = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
_API_URL = "https://api.github.com/repos/dscripka/openWakeWord/releases/assets/"
_MIRROR_URL = "https://huggingface.co/harvestsu/openwakeword-onnx/resolve/main/"
_PINNED = {
    # file name: (sha256, GitHub asset id)
    "melspectrogram.onnx": ("ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f", 129656233),
    "embedding_model.onnx": ("70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f", 129656245),
    "hey_jarvis_v0.1.onnx": ("94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb", 129656242),
}


# --------------------------------------------------------------------------- model files

def models_dir() -> str:
    """openWakeWord's own resources/models folder (where its tools look too)."""
    import importlib.util

    spec = importlib.util.find_spec("openwakeword")  # locate without importing the package
    if spec is None or not spec.submodule_search_locations:
        raise ImportError("openwakeword is not installed (pip install openwakeword)")
    return os.path.join(list(spec.submodule_search_locations)[0], "resources", "models")


def _import_model_class():
    """Import ``openwakeword.model.Model`` without its training helper.

    ``openwakeword/__init__`` imports ``custom_verifier_model``, which pulls in
    scikit-learn (~1.3 s) only to *train* verifiers. A stub stands in during
    the import; the real module still loads if anything calls it later.
    """
    import sys
    import types

    name = "openwakeword.custom_verifier_model"
    if "openwakeword" in sys.modules or name in sys.modules:
        from openwakeword.model import Model

        return Model

    def train_custom_verifier(*args, **kwargs):
        sys.modules.pop(name, None)
        from openwakeword.custom_verifier_model import train_custom_verifier as real

        return real(*args, **kwargs)

    stub = types.ModuleType(name)
    stub.train_custom_verifier = train_custom_verifier
    sys.modules[name] = stub
    try:
        from openwakeword.model import Model
    finally:
        if sys.modules.get(name) is stub:
            del sys.modules[name]
    return Model


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _looks_like_model(data_or_path) -> bool:
    if isinstance(data_or_path, (bytes, bytearray)):
        head, size = bytes(data_or_path[:64]), len(data_or_path)
    else:
        if not os.path.isfile(data_or_path):
            return False
        size = os.path.getsize(data_or_path)
        with open(data_or_path, "rb") as f:
            head = f.read(64)
    # The stock downloader happily saves "504 Gateway Time-out" HTML pages.
    return size > 50_000 and not head.lstrip().lower().startswith((b"<", b"{"))


def _is_good(path: str, sha: str | None) -> bool:
    if not _looks_like_model(path):
        return False
    return sha is None or _sha256(path) == sha


def _fetch(url: str, headers: dict | None = None, timeout: float = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "jarvis-audio/1.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def ensure_model_file(name: str, directory: str | None = None) -> str:
    """Make sure ``name`` (e.g. 'hey_jarvis_v0.1.onnx') exists and is intact; download if not."""
    directory = directory or models_dir()
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    sha, asset_id = _PINNED.get(name, (None, None))
    if _is_good(path, sha):
        return path

    sources = [(_RELEASE_URL + name, {})]
    if asset_id:
        sources.append((f"{_API_URL}{asset_id}", {"Accept": "application/octet-stream"}))
        sources.append((_MIRROR_URL + name, {}))  # only with a pinned hash to check against
    errors = []
    for url, headers in sources:
        for attempt in range(2):
            try:
                data = _fetch(url, headers)
                if not _looks_like_model(data):
                    raise ValueError(f"not a model file ({len(data)} bytes)")
                if sha and hashlib.sha256(data).hexdigest() != sha:
                    raise ValueError("SHA-256 mismatch")
                tmp = path + ".part"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, path)
                log.info("Downloaded %s (%d KB) from %s", name, len(data) // 1024, url.split("/")[2])
                return path
            except Exception as exc:
                errors.append(f"{url}: {exc}")
                time.sleep(1.0 + attempt)
    raise RuntimeError(f"Could not download wake-word model {name}:\n  " + "\n  ".join(errors))


def ensure_models(model: str = "hey_jarvis") -> dict:
    """Return paths of the feature models and the wake-word model, downloading as needed.

    ``model`` is a pretrained name ('hey_jarvis', 'alexa', ...) or a path to a
    custom openWakeWord .onnx file.
    """
    paths = {
        "melspec": ensure_model_file("melspectrogram.onnx"),
        "embedding": ensure_model_file("embedding_model.onnx"),
    }
    if model.lower().endswith(".onnx") and os.path.isfile(model):
        paths["wakeword"] = model
    else:
        paths["wakeword"] = ensure_model_file(f"{model.replace(' ', '_')}_v0.1.onnx")
    return paths


# --------------------------------------------------------------------------- mic

def _open_mic(device, blocksize: int, on_block):
    """Open a 16 kHz mono int16 input stream; resample if the device refuses 16 kHz.

    ``on_block(int16_array)`` is called from the audio thread.
    """
    def cb16(indata, _frames, _time, _status):
        on_block(indata[:, 0].copy())

    try:
        return sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                              blocksize=blocksize, device=device, callback=cb16)
    except Exception as exc:
        native = int(sd.query_devices(device, "input")["default_samplerate"])
        log.info("Mic refused %d Hz (%s); recording at %d Hz and resampling", SAMPLE_RATE, exc, native)
        ratio = SAMPLE_RATE / native

        def cbn(indata, frames, _time, _status):
            x = indata[:, 0].astype(np.float32)
            n_out = max(1, int(round(frames * ratio)))
            y = np.interp(np.linspace(0, frames - 1, n_out), np.arange(frames), x)
            on_block(y.astype(np.int16))

        return sd.InputStream(samplerate=native, channels=1, dtype="int16",
                              blocksize=int(round(blocksize / ratio)), device=device, callback=cbn)


# --------------------------------------------------------------------------- detector

class WakeWord:
    """'Hey Jarvis' detector + push-to-talk hotkey.

    ``wait()`` blocks until either fires. ``start_background(callback)`` calls
    ``callback()`` on every trigger from a worker thread (keep it quick, e.g.
    ``speaker.stop``). Both can be active at once; the mic stays open only
    while at least one of them is.
    """

    def __init__(self, model: str = "hey_jarvis", threshold: float = 0.5,
                 hotkey: str | None = "ctrl+alt+space", device=None, cooldown: float = 1.5):
        t0 = time.perf_counter()
        Model = _import_model_class()
        paths = ensure_models(model)
        self._oww = Model(
            wakeword_models=[paths["wakeword"]],
            inference_framework="onnx",
            melspec_model_path=paths["melspec"],
            embedding_model_path=paths["embedding"],
        )
        self._key = next(iter(self._oww.models))
        self.model_name = model
        self.threshold = threshold
        self.hotkey = hotkey
        self.device = device
        self.cooldown = cooldown
        self.last_trigger: tuple[str, float] | None = None  # (source, score)

        self._lock = threading.Lock()
        self._model_lock = threading.Lock()
        self._subs: dict[int, callable] = {}
        self._ids = itertools.count(1)
        self._bg_token: int | None = None
        self._thread: threading.Thread | None = None
        self._halt: threading.Event | None = None
        self._hotkey_handle = None
        self._mic_error: str | None = None
        self._quiet_until = 0.0
        log.info("Wake word ready: %s (threshold %.2f, hotkey %s) in %.1fs",
                 self._key, threshold, hotkey or "off", time.perf_counter() - t0)

    # ---- public API

    def wait(self, timeout: float | None = None) -> str | None:
        """Block until the wake word or hotkey fires. Returns 'wake' or 'hotkey', None on timeout."""
        fired = threading.Event()
        token = self._subscribe(fired.set)
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            while not fired.wait(0.1):
                if self._mic_error and self._hotkey_handle is None:
                    raise RuntimeError(f"Microphone unavailable and no hotkey: {self._mic_error}")
                if deadline is not None and time.monotonic() >= deadline:
                    return None
        finally:
            self._unsubscribe(token)
        return self.last_trigger[0] if self.last_trigger else "wake"

    def start_background(self, callback) -> None:
        """Call ``callback()`` on each trigger until ``stop_background()``."""
        self.stop_background()
        self._bg_token = self._subscribe(callback)

    def stop_background(self) -> None:
        token, self._bg_token = self._bg_token, None
        if token is not None:
            self._unsubscribe(token)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def score_clip(self, audio) -> float:
        """Highest wake-word score over a 16 kHz clip (int16 or float in [-1, 1]). For tests."""
        audio = np.asarray(audio)
        if audio.dtype != np.int16:
            audio = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
        pad = np.zeros(SAMPLE_RATE, dtype=np.int16)
        audio = np.concatenate([pad, audio, pad])
        best = 0.0
        with self._model_lock:
            self._oww.reset()
            for i in range(0, len(audio) - FRAME + 1, FRAME):
                best = max(best, float(self._oww.predict(audio[i:i + FRAME])[self._key]))
            self._oww.reset()
        return best

    def close(self) -> None:
        with self._lock:
            self._subs.clear()
        self._bg_token = None
        self._stop_thread()

    # ---- internals

    def _subscribe(self, fn) -> int:
        with self._lock:
            token = next(self._ids)
            self._subs[token] = fn
            if not self.running:
                self._mic_error = None
                self._halt = threading.Event()
                self._thread = threading.Thread(target=self._run, args=(self._halt,),
                                                name="jarvis-wake", daemon=True)
                self._thread.start()
                self._register_hotkey()
        return token

    def _unsubscribe(self, token: int) -> None:
        with self._lock:
            self._subs.pop(token, None)
            idle = not self._subs
        if idle:
            self._stop_thread()

    def _stop_thread(self) -> None:
        with self._lock:
            if self._subs:
                return
            thread, halt = self._thread, self._halt
            self._thread = None
            self._unregister_hotkey()
        if halt:
            halt.set()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2)

    def _register_hotkey(self) -> None:
        if not self.hotkey or self._hotkey_handle is not None:
            return
        try:
            import keyboard

            self._hotkey_handle = keyboard.add_hotkey(self.hotkey, self._on_hotkey)
        except Exception as exc:
            log.warning("Push-to-talk hotkey %r unavailable: %s", self.hotkey, exc)

    def _unregister_hotkey(self) -> None:
        if self._hotkey_handle is None:
            return
        try:
            import keyboard

            keyboard.remove_hotkey(self._hotkey_handle)
        except Exception:
            pass
        self._hotkey_handle = None

    def _on_hotkey(self) -> None:
        now = time.monotonic()
        if now < self._quiet_until:
            return
        self._quiet_until = now + 0.5
        self._fire("hotkey", 1.0)

    def _fire(self, source: str, score: float) -> None:
        self.last_trigger = (source, score)
        log.info("Wake trigger: %s (score %.2f)", source, score)
        with self._lock:
            callbacks = list(self._subs.values())
        for cb in callbacks:
            try:
                cb()
            except Exception:
                log.exception("Wake-word callback failed")

    def _run(self, halt: threading.Event) -> None:
        frames: queue.Queue = queue.Queue(maxsize=64)
        pending = np.zeros(0, dtype=np.int16)

        def on_block(block):
            try:
                frames.put_nowait(block)
            except queue.Full:
                pass  # detector fell behind; dropping audio beats growing latency

        try:
            stream = _open_mic(self.device, FRAME, on_block)
        except Exception as exc:
            self._mic_error = str(exc)
            log.error("Wake word: cannot open microphone (%s). Hotkey still works.", exc)
            return
        with self._model_lock:
            self._oww.reset()
        try:
            with stream:
                while not halt.is_set():
                    try:
                        block = frames.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    pending = np.concatenate([pending, block])
                    while pending.size >= FRAME:
                        frame, pending = pending[:FRAME], pending[FRAME:]
                        with self._model_lock:
                            score = float(self._oww.predict(frame)[self._key])
                        now = time.monotonic()
                        if score >= self.threshold and now >= self._quiet_until:
                            self._quiet_until = now + self.cooldown
                            with self._model_lock:
                                self._oww.reset()
                            self._fire("wake", score)
        except Exception as exc:
            self._mic_error = str(exc)
            log.error("Wake word stopped: %s", exc)
