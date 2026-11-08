"""JARVIS voice layer: text-to-speech, speech-to-text and wake-word detection.

    from jarvis.audio.tts import Speaker      # edge-tts -> miniaudio -> sounddevice
    from jarvis.audio.stt import Listener     # sounddevice -> silero VAD -> faster-whisper
    from jarvis.audio.wake import WakeWord    # openWakeWord "hey jarvis" + push-to-talk hotkey

Submodules are imported lazily, so ``import jarvis.audio`` stays cheap and
``from jarvis.audio import Speaker`` only loads the TTS dependencies.
"""

__all__ = ["Speaker", "Listener", "WakeWord"]

_LAZY = {"Speaker": "tts", "Listener": "stt", "WakeWord": "wake"}


def __getattr__(name):
    if name in _LAZY:
        import importlib

        module = importlib.import_module(f"{__name__}.{_LAZY[name]}")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
