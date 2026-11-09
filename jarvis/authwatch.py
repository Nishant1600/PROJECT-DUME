"""Auth watchdog (#51): keep the headless Claude sign-in alive.

JARVIS drives the Claude Code CLI unattended (`claude -p`). The normal interactive
sign-in *expires periodically and silently*, which would leave the always-on brain
dead. This module tracks the known expiry, notices auth failures in turn errors, and
tells you — in good time — to run `claude setup-token` (a long-lived token for
unattended use).

Nothing here changes your credentials or runs `setup-token` for you (that's
interactive and yours to do). It only watches and advises.

    python -m jarvis.authwatch status            # days left, and whether to re-auth
    python -m jarvis.authwatch set 2026-11-11     # record a known expiry date
    python -m jarvis.authwatch note "<cli text>"  # learn an expiry from CLI output
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys

from .config import RUNTIME_DIR

AUTH_FILE = RUNTIME_DIR / "auth.json"

# The CLI's own wording when the interactive login is about to lapse, e.g.
# "...can end at any time from now until 2026-11-11..." / "expires on 2026-11-11".
_EXPIRY_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")

# Phrases in a turn error that mean "the brain couldn't authenticate" (not a network drop).
_AUTH_ERROR_CUES = (
    "setup-token", "setup token", "claude login", "please run claude",
    "not logged in", "log in to claude", "authentication", "unauthorized",
    "oauth token", "token has expired", "token expired", "invalid api key",
    "credential", "re-authenticate", "reauthenticate", "session expired",
    "401", "403 forbidden",
)


def parse_expiry(text: str) -> datetime.date | None:
    """Pull an ISO date out of CLI text (used to learn the sign-in's expiry)."""
    m = _EXPIRY_RE.search(text or "")
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def is_auth_error(text: str | None) -> bool:
    """True if a turn error looks like an authentication/expiry failure (not a network issue)."""
    if not text:
        return False
    low = text.lower()
    # a bare network drop is handled elsewhere; don't claim those as auth problems
    if any(n in low for n in ("getaddrinfo", "timed out", "connection reset",
                              "temporary failure in name resolution", "network is unreachable")):
        if not any(c in low for c in ("setup-token", "login", "unauthorized", "token")):
            return False
    return any(cue in low for cue in _AUTH_ERROR_CUES)


def _load() -> dict:
    try:
        return json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    AUTH_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def set_expiry(d: datetime.date) -> None:
    data = _load()
    data["expires"] = d.isoformat()
    data["updated"] = datetime.date.today().isoformat()
    _save(data)


def record_warning(text: str) -> datetime.date | None:
    """If CLI output carries an expiry date, store it. Returns the date learned, or None."""
    d = parse_expiry(text)
    if d:
        set_expiry(d)
    return d


def note_auth_failure(now: datetime.date | None = None) -> None:
    """Record that a turn failed to authenticate (so status reflects a live problem)."""
    now = now or datetime.date.today()
    data = _load()
    data["last_failure"] = now.isoformat()
    _save(data)


def clear_failure() -> None:
    data = _load()
    if data.pop("last_failure", None) is not None:
        _save(data)


def status(now: datetime.date | None = None) -> dict:
    """Current auth standing: days until expiry, and whether re-auth is due."""
    now = now or datetime.date.today()
    data = _load()
    expires = data.get("expires")
    exp_date = None
    days_left = None
    if expires:
        try:
            exp_date = datetime.date.fromisoformat(expires)
            days_left = (exp_date - now).days
        except ValueError:
            exp_date = None
    had_failure = data.get("last_failure") == now.isoformat()
    # re-auth is due if we saw a failure today, or expiry is within three days / past
    needs = had_failure or (days_left is not None and days_left <= 3)
    return {
        "expires": exp_date.isoformat() if exp_date else None,
        "days_left": days_left,
        "needs_setup_token": bool(needs),
        "recent_failure": bool(had_failure),
    }


def advice(now: datetime.date | None = None) -> str | None:
    """A short spoken line if the sign-in needs attention, else None."""
    s = status(now)
    if not s["needs_setup_token"]:
        return None
    if s["recent_failure"]:
        return ("Sir, my Claude sign-in just failed to authenticate. Please run "
                "`claude setup-token` in a terminal so I can keep working unattended.")
    d = s["days_left"]
    when = ("has expired" if d is not None and d < 0
            else "expires today" if d == 0
            else f"expires in {d} day{'s' if d != 1 else ''}")
    return (f"Sir, my Claude sign-in {when}. Run `claude setup-token` for a long-lived token "
            "so I don't go quiet.")


# --------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jarvis.authwatch", description="Watch the headless Claude sign-in")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("set", help="record a known expiry date (YYYY-MM-DD)")
    s.add_argument("date")
    n = sub.add_parser("note", help="learn an expiry from CLI output text")
    n.add_argument("text", nargs="+")
    st = sub.add_parser("status", help="days left + whether to re-auth")
    st.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "set":
        d = parse_expiry(args.date)
        if not d:
            print("Give a date as YYYY-MM-DD.")
            return 1
        set_expiry(d)
        print(f"Recorded sign-in expiry: {d.isoformat()}.")
        return 0
    if args.cmd == "note":
        d = record_warning(" ".join(args.text))
        print(f"Learned expiry {d.isoformat()}." if d else "No date found in that text.")
        return 0

    # default: status
    s = status()
    if getattr(args, "json", False):
        print(json.dumps(s, indent=2))
        return 0
    if s["expires"]:
        print(f"Sign-in expires: {s['expires']} ({s['days_left']} days left).")
    else:
        print("No expiry recorded yet. Record one with: jarvis.authwatch set YYYY-MM-DD")
    tip = advice()
    if tip:
        print("\n" + tip)
    return 0


if __name__ == "__main__":
    sys.exit(main())
