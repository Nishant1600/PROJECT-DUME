"""Load config.yaml with defaults; resolve paths relative to the DUME root."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = ROOT / "runtime"
MEMORY_DIR = ROOT / "memory"
LOGS_DIR = ROOT / "logs"
STOP_FILE = LOGS_DIR / "STOP"
PERSONA_FILE = ROOT / "jarvis" / "persona.md"

DEFAULTS: dict[str, Any] = {
    "address_as": "",
    "claude": {
        "command": "claude",
        "model": None,
        "effort": None,
        "summary_model": "haiku",
        "tools": "Bash,PowerShell,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch",
        "allowed_tools": [
            "Read", "Glob", "Grep", "WebSearch", "WebFetch",
            "mcp__jarvis-desktop__list_windows", "mcp__jarvis-desktop__active_window_text",
            "mcp__jarvis-desktop__ocr_screen", "mcp__jarvis-desktop__screenshot",
        ],
        "auto_approve_desktop_actions": False,
        "turn_timeout_seconds": 600,
        "extra_args": [],
    },
    "session": {"idle_minutes": 15, "token_cap": 60000, "summary_words": 150},
    "project_dir": "workspace",
    "projects": {},
    "voice": {
        "tts_voice": "en-GB-RyanNeural",
        "tts_rate": "+0%",
        "whisper_model": "small",
        "whisper_device": "cuda",
        "whisper_compute_type": "float16",
        "language": None,
        "wake_model": "hey_jarvis",
        "wake_threshold": 0.5,
        "push_to_talk_hotkey": "ctrl+alt+space",
    },
    "desktop": {"enabled": True, "kill_switch_hotkey": "ctrl+alt+j"},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def resolve_path(p: str | Path) -> Path:
    path = Path(p).expanduser()
    return path if path.is_absolute() else (ROOT / path).resolve()


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else ROOT / "config.yaml"
    data: dict = {}
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    cfg = _merge(DEFAULTS, data)
    for d in (RUNTIME_DIR, MEMORY_DIR, LOGS_DIR, resolve_path(cfg["project_dir"])):
        d.mkdir(parents=True, exist_ok=True)
    return cfg
