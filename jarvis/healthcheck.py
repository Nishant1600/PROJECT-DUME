"""Self health-check (#52): "are you okay?"

Verifies the moving parts JARVIS depends on — voice models, GPU, the Claude CLI,
connectors, disk space, offline fallback — and reports, in one spoken line, what (if
anything) needs attention. Every probe is local, defensive and model-free, so asking
"are you okay?" never costs usage and never throws.

    python -m jarvis.healthcheck          # human-readable report
    python -m jarvis.healthcheck --json
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys

# --------------------------------------------------------------------------- triggers

_TRIGGER = re.compile(
    r"^(?:/health(?:check)?|/selftest|"
    r"are you (?:ok|okay|alright|all right|well|healthy|working|there|good)|"
    r"(?:run (?:a )?)?(?:health|system|self)[\s-]?(?:check|test|report)|"
    r"self[\s-]?test|diagnostics?|system status|check yourself)\W*$", re.I)


def is_trigger(text: str) -> bool:
    return bool(_TRIGGER.match((text or "").strip()))


# --------------------------------------------------------------------------- probes

def _module_ok(name: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _gpu() -> dict:
    """Is a CUDA GPU available for faster speech-to-text? (torch is optional.)"""
    if not _module_ok("torch"):
        return {"status": "warn", "check": "GPU", "detail": "torch not installed — speech-to-text runs on CPU"}
    try:
        import torch
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            return {"status": "pass", "check": "GPU", "detail": f"CUDA ready ({name})"}
        return {"status": "warn", "check": "GPU", "detail": "no CUDA device — speech-to-text runs on CPU"}
    except Exception as e:  # torch present but broken
        return {"status": "warn", "check": "GPU", "detail": f"torch error: {str(e)[:80]}"}


def _disk(path: str = ".") -> dict:
    try:
        usage = shutil.disk_usage(path)
        free_gb = usage.free / (1024 ** 3)
        if free_gb < 1:
            return {"status": "fail", "check": "disk", "detail": f"only {free_gb:.1f} GB free"}
        if free_gb < 5:
            return {"status": "warn", "check": "disk", "detail": f"{free_gb:.1f} GB free (getting low)"}
        return {"status": "pass", "check": "disk", "detail": f"{free_gb:.0f} GB free"}
    except Exception as e:
        return {"status": "warn", "check": "disk", "detail": f"couldn't read: {str(e)[:60]}"}


def _cli(name: str, label: str, required: bool) -> dict:
    if shutil.which(name):
        return {"status": "pass", "check": label, "detail": "on PATH"}
    sev = "fail" if required else "warn"
    return {"status": sev, "check": label, "detail": "not found on PATH"}


def _mic() -> dict:
    """Any input device for the microphone? (sounddevice is optional in text/no-voice mode.)"""
    if not _module_ok("sounddevice"):
        return {"status": "warn", "check": "microphone", "detail": "sounddevice not installed (text mode only)"}
    try:
        import sounddevice as sd
        ins = [d for d in sd.query_devices() if d.get("max_input_channels", 0) > 0]
        if ins:
            return {"status": "pass", "check": "microphone", "detail": f"{len(ins)} input device(s)"}
        return {"status": "warn", "check": "microphone", "detail": "no input device found"}
    except Exception as e:
        return {"status": "warn", "check": "microphone", "detail": f"query failed: {str(e)[:60]}"}


def _ollama(cfg: dict | None) -> dict:
    """Is the optional local Ollama model reachable, for the zero-cloud fallback?"""
    try:
        import urllib.request
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1.0) as r:
            r.read(1)
        return {"status": "pass", "check": "offline fallback", "detail": "Ollama is reachable"}
    except Exception:
        return {"status": "info", "check": "offline fallback", "detail": "Ollama not running (optional)"}


def run_checks(cfg: dict | None = None) -> dict:
    """Run every probe and return {status, summary, checks:[...]}.

    `status` is the worst of the individual checks (fail > warn > info/pass)."""
    cfg = cfg or {}
    checks = [
        _cli("claude", "Claude CLI", required=True),
        _disk("."),
        _gpu(),
        {"status": "pass" if _module_ok("faster_whisper") else "warn", "check": "speech-to-text",
         "detail": "faster-whisper ready" if _module_ok("faster_whisper") else "faster-whisper not installed"},
        {"status": "pass" if _module_ok("edge_tts") else "warn", "check": "text-to-speech",
         "detail": "edge-tts ready" if _module_ok("edge_tts") else "edge-tts not installed"},
        {"status": "pass" if _module_ok("openwakeword") else "warn", "check": "wake word",
         "detail": "openWakeWord ready" if _module_ok("openwakeword") else "openwakeword not installed"},
        _mic(),
        _cli("gh", "GitHub CLI", required=False),
    ]
    # auth standing (expiry / recent failure)
    try:
        from .authwatch import status as auth_status
        a = auth_status()
        if a["needs_setup_token"]:
            d = a["days_left"]
            det = ("sign-in failed recently" if a["recent_failure"]
                   else f"expires in {d} day(s)" if d is not None else "re-auth recommended")
            checks.append({"status": "fail", "check": "Claude sign-in", "detail": det + " — run claude setup-token"})
        elif a["expires"]:
            checks.append({"status": "pass", "check": "Claude sign-in", "detail": f"valid to {a['expires']}"})
    except Exception:
        pass
    # connectors (informational — we can't probe the account cheaply)
    if (cfg.get("integrations") or {}).get("use_my_connectors"):
        checks.append({"status": "info", "check": "connectors", "detail": "enabled (Gmail/Notion/Calendar/Drive)"})
    checks.append(_ollama(cfg))

    rank = {"fail": 3, "warn": 2, "info": 1, "pass": 0}
    worst = max((rank.get(c["status"], 0) for c in checks), default=0)
    overall = {3: "fail", 2: "warn", 1: "pass", 0: "pass"}[worst]
    return {"status": overall, "summary": spoken(checks, cfg), "checks": checks}


def spoken(checks: list[dict], cfg: dict | None = None) -> str:
    who = ((cfg or {}).get("address_as") or "Sir")
    problems = [c for c in checks if c["status"] in ("fail", "warn")]
    if not problems:
        return f"All systems healthy, {who.lower()}."
    bad = [c for c in problems if c["status"] == "fail"]
    lead = bad or problems
    parts = [f"{c['check']} — {c['detail']}" for c in lead[:3]]
    head = "Something needs attention" if bad else "A couple of things to note"
    return f"{head}, {who.lower()}: " + "; ".join(parts) + "."


# --------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jarvis.healthcheck", description="JARVIS self health-check")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    cfg = {}
    try:
        from .config import load_config
        cfg = load_config()
    except Exception:
        pass

    rep = run_checks(cfg)
    if args.json:
        print(json.dumps(rep, indent=2))
        return 0 if rep["status"] != "fail" else 2
    mark = {"pass": "OK ", "warn": "WARN", "fail": "FAIL", "info": "·  "}
    print(f"JARVIS health — {rep['status'].upper()}\n")
    for c in rep["checks"]:
        print(f"  [{mark.get(c['status'], '?')}] {c['check']}: {c['detail']}")
    print("\n" + rep["summary"])
    return 0 if rep["status"] != "fail" else 2


if __name__ == "__main__":
    sys.exit(main())
