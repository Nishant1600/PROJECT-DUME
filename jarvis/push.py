"""Reach your phone (#61): push a brief, a nudge or a finished job to your phone.

Opt-in and dependency-free (stdlib urllib only). Pick one service in config and JARVIS
can send you a short notification — e.g. when a long task finishes, or your scheduled
morning brief is ready and you're away from the desk.

config.yaml:
  push:
    enabled: true
    service: ntfy            # ntfy | pushover | telegram
    ntfy_topic: my-jarvis-abc123       # ntfy.sh topic (pick something unguessable)
    pushover_token: ...
    pushover_user: ...
    telegram_token: ...
    telegram_chat: ...

    python -m jarvis.push "Build finished" --title JARVIS
"""
from __future__ import annotations

import argparse
import sys
import urllib.parse
import urllib.request

SERVICES = ("ntfy", "pushover", "telegram")


def is_configured(cfg: dict | None) -> bool:
    p = (cfg or {}).get("push") or {}
    if not p.get("enabled"):
        return False
    svc = (p.get("service") or "").lower()
    if svc == "ntfy":
        return bool(p.get("ntfy_topic"))
    if svc == "pushover":
        return bool(p.get("pushover_token") and p.get("pushover_user"))
    if svc == "telegram":
        return bool(p.get("telegram_token") and p.get("telegram_chat"))
    return False


def build_request(cfg: dict, title: str, message: str) -> tuple[str, bytes, dict]:
    """Return (url, body_bytes, headers) for the configured service. Pure — no network.

    Raises ValueError if push isn't configured."""
    p = (cfg or {}).get("push") or {}
    svc = (p.get("service") or "").lower()
    title = (title or "JARVIS").strip()
    message = (message or "").strip()
    if svc == "ntfy":
        topic = p["ntfy_topic"]
        base = (p.get("ntfy_server") or "https://ntfy.sh").rstrip("/")
        return (f"{base}/{topic}", message.encode("utf-8"),
                {"Title": title, "Priority": "default"})
    if svc == "pushover":
        data = urllib.parse.urlencode({
            "token": p["pushover_token"], "user": p["pushover_user"],
            "title": title, "message": message}).encode("utf-8")
        return ("https://api.pushover.net/1/messages.json", data,
                {"Content-Type": "application/x-www-form-urlencoded"})
    if svc == "telegram":
        data = urllib.parse.urlencode({
            "chat_id": p["telegram_chat"],
            "text": f"{title}: {message}" if title else message}).encode("utf-8")
        return (f"https://api.telegram.org/bot{p['telegram_token']}/sendMessage", data,
                {"Content-Type": "application/x-www-form-urlencoded"})
    raise ValueError(f"push not configured (service={svc!r})")


def send(title: str, message: str, cfg: dict | None, timeout: float = 8.0) -> bool:
    """Send a push notification. Returns True on success, False on any failure."""
    if not is_configured(cfg):
        return False
    try:
        url, body, headers = build_request(cfg, title, message)
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jarvis.push", description="Push a notification to your phone")
    ap.add_argument("message", nargs="+")
    ap.add_argument("--title", default="JARVIS")
    args = ap.parse_args(argv)
    cfg = {}
    try:
        from .config import load_config
        cfg = load_config()
    except Exception:
        pass
    if not is_configured(cfg):
        print("Push isn't configured. Set push.enabled and a service in config.yaml.")
        return 1
    ok = send(args.title, " ".join(args.message), cfg)
    print("Sent." if ok else "Couldn't send the push.")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
