"""A tiny pub/sub event bus connecting the voice loop, the policy and the HUD.

The orchestrator publishes events (state changes, transcript lines, tool activity,
confirmation requests); the UI server subscribes and streams them to the browser.
The HUD posts replies (allow/deny, typed text, stop) back as inbound events.
Thread-safe; subscribers get their own queue and never block the publisher.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any

# ---- event type constants (also used as the SSE "event:" name) -------------
STATE = "state"            # {state: idle|listening|thinking|speaking|acting, detail?}
LEVEL = "level"            # {rms: 0..1}  microphone loudness, for the orb
PARTIAL = "partial"        # {text}       live (incomplete) transcript of what you're saying
TRANSCRIPT = "transcript"  # {role: user|jarvis|system, text, final: bool}
TOOL = "tool"              # {name, summary, phase: start|done, detail?}
CONFIRM = "confirm"        # {id, spoken, detail, tool}   -> HUD shows Allow/Deny
CONFIRM_RESULT = "confirm_result"  # {id, allow}   resolved (from HUD or voice)
NOTICE = "notice"          # {text, level: info|warn|error}
SESSION = "session"        # {turns, context_tokens, token_cap, project}
LOG = "log"                # {line}       raw activity line for the side ticker

STATES = ("idle", "listening", "thinking", "speaking", "acting")


@dataclass
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


class Bus:
    def __init__(self, history: int = 60):
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._history: list[Event] = []
        self._history_max = history
        self.state = "idle"

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._lock:
            self._subs.append(q)
            # Replay recent context so a browser that connects late is in sync.
            for ev in self._history:
                try:
                    q.put_nowait(ev)
                except queue.Full:
                    break
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, type_: str, **data: Any) -> None:
        ev = Event(type_, data)
        with self._lock:
            # Persist only durable context for late-joining browsers; a confirm is
            # live (answering it is time-bound) and partial/level are transient.
            if type_ in (STATE, TRANSCRIPT, TOOL, SESSION, NOTICE):
                self._history.append(ev)
                del self._history[: -self._history_max]
            if type_ == STATE:
                self.state = data.get("state", self.state)
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass  # a stalled browser must never block JARVIS

    # convenience wrappers ---------------------------------------------------
    def set_state(self, state: str, detail: str = "") -> None:
        self.publish(STATE, state=state, detail=detail)

    def say_line(self, role: str, text: str, final: bool = True) -> None:
        if text:
            self.publish(TRANSCRIPT, role=role, text=text, final=final)
