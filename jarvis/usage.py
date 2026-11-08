"""Usage radar for JARVIS (README roadmap: "how your Claude usage is trending and when a session will rotate").

JARVIS logs a tiny record after each turn (timestamp, session turns, context size, the turn's
API-equivalent cost) to runtime/usage.jsonl, and this reports it back on "usage" / "how's my
usage": how busy today is versus your recent average, and how close the current session is to
rotating (context cap or idle timeout). It's a local command — no Claude call, so checking your
usage never adds to it.

Note: you're on a Claude subscription, so the dollar figure is an API-equivalent estimate for
trend only, not your actual bill.

    python -m jarvis.usage              # print the radar
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime

from .config import RUNTIME_DIR

USAGE_FILE = RUNTIME_DIR / "usage.jsonl"
MAX_LINES = 4000

_TRIGGER = re.compile(
    r"^(?:/usage|usage(?:\s+radar)?|token\s+usage|"
    r"how(?:'s| is)?\s+my\s+usage|how\s+much\s+have\s+i\s+used|"
    r"when\s+(?:will|does)\s+(?:the\s+)?session\s+rotate|how\s+close\s+(?:am\s+i\s+)?to\s+rotating)\W*$",
    re.I)


def is_trigger(text: str) -> bool:
    return bool(_TRIGGER.match((text or "").strip()))


def log(context_tokens: int, cost_usd: float | None, session_id: str | None) -> None:
    """Append one turn's usage. Best-effort; never raises into the turn."""
    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.time(), "ctx": int(context_tokens or 0),
               "cost": float(cost_usd) if cost_usd else 0.0, "sid": session_id or ""}
        with open(USAGE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        _trim()
    except Exception:
        pass


def _trim() -> None:
    try:
        lines = USAGE_FILE.read_text(encoding="utf-8").splitlines()
        if len(lines) > MAX_LINES:
            USAGE_FILE.write_text("\n".join(lines[-MAX_LINES:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def _records() -> list[dict]:
    out = []
    try:
        for raw in USAGE_FILE.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(raw))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def radar(status: dict | None = None) -> dict:
    """Compute the usage picture: today vs recent average, plus rotation proximity."""
    recs = _records()
    now = datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    today = [r for r in recs if r.get("ts", 0) >= midnight]
    today_turns = len(today)
    today_cost = sum(r.get("cost", 0.0) for r in today)
    today_sessions = len({r.get("sid") for r in today if r.get("sid")})

    # per-day turn counts over the previous 7 days (not today) -> average
    day_counts: dict[str, int] = {}
    for r in recs:
        ts = r.get("ts", 0)
        if ts < midnight and ts >= midnight - 7 * 86400:
            day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            day_counts[day] = day_counts.get(day, 0) + 1
    avg_daily = (sum(day_counts.values()) / len(day_counts)) if day_counts else 0.0

    if avg_daily == 0:
        trend = "just getting started" if today_turns else "nothing logged yet"
    elif today_turns > 1.3 * avg_daily:
        trend = "busier than usual"
    elif today_turns < 0.6 * avg_daily:
        trend = "quieter than usual"
    else:
        trend = "about average"

    out = {"today_turns": today_turns, "today_cost": round(today_cost, 2),
           "today_sessions": today_sessions, "avg_daily_turns": round(avg_daily, 1),
           "trend": trend}

    if status:
        ctx = int(status.get("context_tokens") or 0)
        cap = int(status.get("token_cap") or 0)
        out["context_pct"] = round(100 * ctx / cap) if cap else None
        out["turns_this_session"] = status.get("turns")
    return out


def spoken(status: dict | None = None, cfg: dict | None = None) -> str:
    r = radar(status)
    if r["today_turns"] == 0:
        return "No usage logged yet today — ask me a few things and I'll start tracking it."
    parts = [f"Today: {r['today_turns']} turn{'s' if r['today_turns'] != 1 else ''}"]
    if r["today_sessions"] > 1:
        parts[0] += f" across {r['today_sessions']} sessions"
    parts[0] += f", {r['trend']}"
    if r.get("today_cost"):
        parts[0] += f" (about ${r['today_cost']:.2f} API-equivalent)"
    line1 = parts[0] + "."
    pct = r.get("context_pct")
    idle_cap = int((cfg or {}).get("session", {}).get("idle_minutes", 15)) if cfg else 15
    if pct is not None:
        line2 = (f"This session is at {pct}% of the context cap; it rotates there or after "
                 f"{idle_cap} idle minutes.")
    else:
        line2 = f"The session rotates at the context cap or after {idle_cap} idle minutes."
    return f"{line1} {line2}"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    r = radar()
    if "--json" in (argv or sys.argv[1:]):
        print(json.dumps(r, indent=2))
    else:
        print(spoken())
        print(json.dumps(r, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
