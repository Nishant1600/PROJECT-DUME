"""System-tray presence for JARVIS (feature #19).

A quiet tray icon whose dot changes colour with JARVIS's state (the same palette as
the HUD). The HUD window opens only when you pick "Show HUD", so with
``--tray --no-window`` JARVIS starts invisibly and just listens for "Hey Jarvis".

Needs pystray + Pillow; if they're missing, `available()` returns False and the app
simply runs without a tray.
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

# matches the HUD core colours
STATE_COLORS = {
    "idle": (74, 144, 217),
    "listening": (84, 214, 255),
    "thinking": (167, 139, 250),
    "speaking": (56, 230, 255),
    "acting": (245, 180, 60),
}


def available() -> bool:
    try:
        import PIL  # noqa: F401
        import pystray  # noqa: F401
        return True
    except Exception:
        return False


def _dot(color: tuple[int, int, int], size: int = 64):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = size // 8
    draw.ellipse([pad, pad, size - pad, size - pad], fill=color + (255,))
    draw.ellipse([pad, pad, size - pad, size - pad], outline=(255, 255, 255, 60), width=max(1, size // 32))
    return img


class TrayPresence:
    """A detached tray icon that mirrors bus state and offers Show HUD / New session / Quit."""

    def __init__(self, bus, on_show=None, on_new_session=None, on_quit=None):
        self.bus = bus
        self.on_show = on_show
        self.on_new_session = on_new_session
        self.on_quit = on_quit
        self._icon = None
        self._stop = threading.Event()

    def start(self) -> "TrayPresence":
        import pystray

        menu = pystray.Menu(
            pystray.MenuItem("Show HUD", lambda *_: self.on_show and self.on_show(), default=True),
            pystray.MenuItem("New session", lambda *_: self.on_new_session and self.on_new_session()),
            pystray.MenuItem("Quit", lambda *_: self._quit()),
        )
        self._icon = pystray.Icon("jarvis", _dot(STATE_COLORS["idle"]), "JARVIS", menu)
        threading.Thread(target=self._watch_state, name="jarvis-tray-state", daemon=True).start()
        self._icon.run_detached()   # non-blocking; the tray runs on its own thread
        log.info("Tray presence started")
        return self

    def _watch_state(self) -> None:
        last = None
        while not self._stop.wait(0.4):
            state = getattr(self.bus, "state", "idle")
            if state != last and self._icon is not None:
                last = state
                try:
                    self._icon.icon = _dot(STATE_COLORS.get(state, STATE_COLORS["idle"]))
                    self._icon.title = f"JARVIS - {state}"
                except Exception as exc:
                    log.debug("tray update failed: %s", exc)

    def _quit(self) -> None:
        self.stop()
        if self.on_quit:
            self.on_quit()

    def stop(self) -> None:
        self._stop.set()
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
