"""Crash watchdog (#53): keep JARVIS running unattended.

Supervises `python -m jarvis.main`: if it exits unexpectedly it is relaunched, with
exponential backoff and a crash-loop guard so a reproducible startup failure doesn't
spin forever. Every start/exit/restart is logged to logs/watchdog.log.

    python -m jarvis.watchdog                 # supervise `jarvis.main` (passes through extra args)
    python -m jarvis.watchdog --tray --no-window

A clean exit (code 0 — e.g. you said "goodbye") is respected and NOT restarted.
"""
from __future__ import annotations

import datetime
import subprocess
import sys
import time
from pathlib import Path

from .config import LOGS_DIR

WATCHDOG_LOG = LOGS_DIR / "watchdog.log"


class RestartPolicy:
    """Decides whether and when to restart, and detects a crash loop. Pure + testable.

    * backoff grows 1,2,4,… capped at `max_backoff` seconds, and resets after the child
      has run healthily for `healthy_after` seconds.
    * if `max_crashes` crashes happen within `window` seconds, it's a crash loop → stop.
    """

    def __init__(self, max_crashes: int = 5, window: float = 120.0,
                 base_backoff: float = 1.0, max_backoff: float = 60.0,
                 healthy_after: float = 60.0):
        self.max_crashes = max_crashes
        self.window = window
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff
        self.healthy_after = healthy_after
        self._crashes: list[float] = []
        self._consecutive = 0

    def record_run(self, ran_seconds: float) -> None:
        """Call after the child exits healthily/long enough to reset the backoff."""
        if ran_seconds >= self.healthy_after:
            self._consecutive = 0

    def record_crash(self, now: float) -> None:
        self._crashes.append(now)
        self._crashes = [t for t in self._crashes if now - t <= self.window]
        self._consecutive += 1

    def is_crash_loop(self) -> bool:
        return len(self._crashes) >= self.max_crashes

    def backoff(self) -> float:
        n = max(0, self._consecutive - 1)
        return min(self.base_backoff * (2 ** n), self.max_backoff)

    def should_restart(self, exit_code: int | None) -> bool:
        """Restart only on an unexpected (non-zero / signalled) exit, and not in a crash loop."""
        if exit_code == 0:
            return False
        return not self.is_crash_loop()


def _log(msg: str) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    try:
        with open(WATCHDOG_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)


def run(argv: list[str] | None = None, policy: RestartPolicy | None = None) -> int:
    """Supervise jarvis.main, restarting it on crashes. Blocks until a clean stop or crash loop."""
    argv = argv if argv is not None else sys.argv[1:]
    policy = policy or RestartPolicy()
    python = Path(sys.executable)
    cmd = [str(python), "-m", "jarvis.main", *argv]
    _log(f"watchdog starting: {' '.join(cmd)}")

    while True:
        started = time.time()
        try:
            proc = subprocess.run(cmd)
            code = proc.returncode
        except KeyboardInterrupt:
            _log("watchdog interrupted by user — stopping.")
            return 0
        except Exception as e:
            _log(f"failed to launch jarvis.main: {e}")
            code = 1
        ran = time.time() - started
        policy.record_run(ran)

        if code == 0:
            _log(f"jarvis.main exited cleanly after {ran:.0f}s — not restarting.")
            return 0

        policy.record_crash(time.time())
        if not policy.should_restart(code):
            _log(f"jarvis.main crashed {policy.max_crashes} times within "
                 f"{policy.window:.0f}s (crash loop) — giving up. Check the error above.")
            return 1
        delay = policy.backoff()
        _log(f"jarvis.main exited with code {code} after {ran:.0f}s — restarting in {delay:.0f}s.")
        time.sleep(delay)


if __name__ == "__main__":
    sys.exit(run())
