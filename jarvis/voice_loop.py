"""Voice front-end: wake word -> listen -> hand text to the Assistant.

Runs on its own thread so the HUD and its text box stay responsive. Publishes
microphone level and a live partial transcript to the bus while you speak, so
the orb reacts and your words appear as you say them.
"""
from __future__ import annotations

import logging
import threading
import time

from . import bus as B
from .assistant import Assistant

log = logging.getLogger(__name__)


class VoiceLoop:
    def __init__(self, cfg: dict, assistant: Assistant, bus: B.Bus):
        self.cfg = cfg
        self.a = assistant
        self.bus = bus
        self.speaker = assistant.speaker
        self.listener = assistant.listener
        self.wake = None
        self._stop = threading.Event()
        self._bg_on = False
        self._bg_lock = threading.Lock()

    # wake word runs in the background while JARVIS talks/works, to allow barge-in
    def _bg_start(self):
        with self._bg_lock:
            if not self._bg_on and self.wake:
                self.wake.start_background(self._barge_in)
                self._bg_on = True

    def _bg_stop(self):
        with self._bg_lock:
            if self._bg_on and self.wake:
                self.wake.stop_background()
                self._bg_on = False

    def _barge_in(self):
        self.a.interrupted.set()
        if self.speaker:
            self.speaker.stop()
        self.bus.publish(B.NOTICE, text="Yes?", level="info")

    def start(self):
        threading.Thread(target=self._run, name="jarvis-voice", daemon=True).start()

    def stop(self):
        self._stop.set()
        self._bg_stop()

    def _listen_once(self) -> str:
        self.bus.set_state("listening")
        if self.speaker:
            self.speaker.chime()
        return self.listener.listen(
            on_level=lambda rms: self.bus.publish(B.LEVEL, rms=rms),
            on_speech_start=lambda: self.bus.set_state("listening", "hearing you"),
            on_partial=lambda t: self.bus.publish(B.PARTIAL, text=t),
        )

    def greeting(self) -> str:
        import datetime
        h = datetime.datetime.now().hour
        part = "Good morning" if h < 12 else "Good afternoon" if h < 17 else "Good evening"
        who = self.cfg.get("address_as") or "sir"
        return f"{part}, {who}. JARVIS online and standing by."

    def _run(self):
        v = self.cfg["voice"]
        if self.speaker:
            self.speaker.say(self.greeting())
        listen_now = False
        while not self._stop.is_set():
            try:
                if not listen_now:
                    self.bus.set_state("idle")
                    self.wake.wait()           # "Hey Jarvis" or push-to-talk
                    if self._stop.is_set():
                        break
                listen_now = False
                if self.speaker:
                    self.speaker.stop()
                text = self._listen_once().strip()
                self.bus.publish(B.PARTIAL, text="")
                if not text:
                    continue
                # listen for barge-in while the Assistant talks/works
                self._bg_start()
                self.a.handle_input(text, source="voice")
                self._bg_stop()
                if self.a.interrupted.is_set():
                    listen_now = True          # the wake word fired mid-reply: listen again now
            except Exception:
                log.exception("voice loop error")
                time.sleep(0.5)
