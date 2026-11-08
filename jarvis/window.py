"""Open the HUD in its own desktop window (no browser tabs, no terminal).

Tries, in order: Microsoft Edge / Chrome / Brave in --app kiosk-ish mode (a clean
frameless-ish app window), then pywebview if installed, then the default browser.
Returns an object with .wait() (blocks until the window closes) and .close().
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_EDGE = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
_CHROME = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]
_BRAVE = [
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
]


def _first_existing(paths) -> str | None:
    for p in paths:
        if os.path.isfile(p):
            return p
    return None


def _browser() -> str | None:
    for group, name in ((_EDGE, "msedge"), (_CHROME, "chrome"), (_BRAVE, "brave")):
        p = _first_existing(group) or shutil.which(name)
        if p:
            return p
    return None


class _ProcWindow:
    def __init__(self, proc: subprocess.Popen, profile: str):
        self.proc = proc
        self.profile = profile

    def wait(self):
        try:
            self.proc.wait()
        except KeyboardInterrupt:
            self.close()

    def close(self):
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass


class _NullWindow:
    def wait(self):
        try:
            while True:
                threading.Event().wait(3600)
        except KeyboardInterrupt:
            pass

    def close(self):
        pass


def open_window(url: str, title: str = "JARVIS"):
    exe = _browser()
    if exe:
        profile = os.path.join(tempfile.gettempdir(), "jarvis-hud-profile")
        Path(profile).mkdir(parents=True, exist_ok=True)
        args = [
            exe, f"--app={url}",
            f"--user-data-dir={profile}",
            "--window-size=1280,800", "--no-first-run", "--no-default-browser-check",
            "--disable-features=Translate,TranslateUI",
            "--autoplay-policy=no-user-gesture-required",  # let the online chime play
        ]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(args, creationflags=flags)
            log.info("Opened HUD in app window via %s", Path(exe).name)
            return _ProcWindow(proc, profile)
        except OSError as e:
            log.warning("App window failed (%s); falling back to default browser", e)
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        log.warning("Could not open a browser; open %s yourself", url)
    return _NullWindow()
