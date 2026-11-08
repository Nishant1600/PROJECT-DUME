"""JARVIS entry point.

    python -m jarvis.main            # HUD window + voice ("Hey Jarvis") + text box
    python -m jarvis.main --no-voice # HUD only, type in the box (no microphone)
    python -m jarvis.main --text     # plain terminal, no HUD (debugging)
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

from . import bus as B
from .assistant import Assistant
from .brain.claude_cli import ClaudeCLI
from .brain.session import SessionManager
from .commands import handle_local
from .config import PERSONA_FILE, ROOT, STOP_FILE, load_config
from .confirm_server import ConfirmServer
from .speech_text import TagStripper

DIM, RESET = "\033[2m", "\033[0m"
CONFIRM_TOOL = "mcp__jarvis-confirm__approve"


def build_mcp_config(cfg: dict, confirm: ConfirmServer) -> Path:
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    python = python if python.exists() else Path(sys.executable)
    env = {"PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"}
    servers = {
        "jarvis-confirm": {
            "command": str(python), "args": ["-m", "jarvis.confirm_mcp"],
            "env": {**env, "JARVIS_CONFIRM_PORT": str(confirm.port),
                    "JARVIS_CONFIRM_TOKEN": confirm.token},
        }
    }
    if cfg["desktop"].get("enabled") and (ROOT / "jarvis" / "desktop_mcp" / "server.py").exists():
        servers["jarvis-desktop"] = {
            "command": str(python), "args": ["-m", "jarvis.desktop_mcp.server"], "env": env}
    path = ROOT / "jarvis-mcp.json"
    path.write_text(json.dumps({"mcpServers": servers}, indent=2), encoding="utf-8")
    return path


def write_guard_settings(cfg: dict) -> Path:
    """Register the PreToolUse guard hook so blocked commands are denied even if pre-approved."""
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    python = python if python.exists() else Path(sys.executable)
    hook_cmd = f'"{python}" -m jarvis.guard_hook'
    settings = {
        "hooks": {
            "PreToolUse": [{
                "matcher": "Bash|PowerShell|Write|Edit|NotebookEdit|mcp__jarvis-desktop__.*|mcp__desktop-commander__.*",
                "hooks": [{"type": "command", "command": hook_cmd, "timeout": 10}],
            }]
        }
    }
    path = ROOT / "jarvis-settings.json"
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return path


def make_brain(cfg: dict, confirm: ConfirmServer) -> ClaudeCLI:
    from .profile import Profile
    mcp = build_mcp_config(cfg, confirm)
    settings = write_guard_settings(cfg)
    profile = Profile(cfg)  # seeds memory/identity.md so JARVIS knows your name
    brain = ClaudeCLI(cfg, PERSONA_FILE, mcp_config=mcp, permission_prompt_tool=CONFIRM_TOOL,
                      extra_system=profile.persona_addendum())
    brain.ccfg["integrations"] = cfg.get("integrations", {})
    brain.ccfg.setdefault("extra_args", [])
    if str(settings) not in brain.ccfg["extra_args"]:
        brain.ccfg["extra_args"] = list(brain.ccfg["extra_args"]) + ["--settings", str(settings)]
    return brain


def start_idle_watcher(sessions: SessionManager, brain: ClaudeCLI, bus: B.Bus) -> None:
    def loop():
        while True:
            time.sleep(30)
            if not brain.busy:
                try:
                    if sessions.maybe_rotate():
                        s = sessions.status()
                        bus.publish(B.SESSION, turns=s["turns"], context_tokens=s["context_tokens"],
                                    token_cap=s["token_cap"],
                                    project=s["project_dir"].replace("\\", "/").split("/")[-1])
                except Exception as e:
                    print(f"{DIM}[rotation failed: {e}]{RESET}")
    threading.Thread(target=loop, daemon=True).start()


# --------------------------------------------------------------------------- terminal mode (debug)

def run_text(assistant: Assistant, confirm: ConfirmServer) -> None:
    from .confirm_server import Answer, Question, parse_answer

    def asker(q: Question) -> Answer:
        print(f"\n\033[33m  Shall I {q.spoken}?  {q.detail[:400]}{RESET}")
        try:
            return parse_answer(input("  [y]es / [n]o / [a]lways: "))
        except EOFError:
            return Answer(False)

    confirm.asker = asker  # terminal confirmations, not the voice/HUD coordinator
    print("JARVIS (text mode). Ctrl+C stops a task; 'quit' exits.")
    while True:
        try:
            line = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); break
        if not line:
            continue
        if line.lower() in ("quit", "exit", "goodbye"):
            break
        from .commands import handle_local
        local = handle_local(line, assistant.sessions)
        if local:
            print(f"jarvis> {local.reply}")
            if local.quit:
                break
            continue
        STOP_FILE.unlink(missing_ok=True)
        stripper = TagStripper()
        print("jarvis> ", end="", flush=True)
        try:
            assistant.sessions.ask(
                line,
                on_text=lambda c: print(stripper.feed(c), end="", flush=True),
                on_tool=lambda n, i: print(f"\n{DIM}  [{n.split('__')[-1]}]{RESET}", flush=True))
        except KeyboardInterrupt:
            print(f"\n{DIM}[stopped]{RESET}"); continue
        print(stripper.flush())
        s = assistant.sessions.status()
        print(f"{DIM}[{s['context_tokens']:,}/{s['token_cap']:,} tokens]{RESET}")


# --------------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description="JARVIS")
    ap.add_argument("--no-voice", action="store_true", help="HUD only, no microphone")
    ap.add_argument("--text", action="store_true", help="plain terminal, no HUD")
    ap.add_argument("--no-window", action="store_true", help="don't open the HUD window (serve only)")
    ap.add_argument("--install-autostart", action="store_true", help="start JARVIS at login")
    ap.add_argument("--uninstall-autostart", action="store_true", help="stop starting at login")
    ap.add_argument("--config")
    args = ap.parse_args()

    if args.install_autostart or args.uninstall_autostart:
        from . import autostart
        if args.install_autostart:
            link = autostart.install()
            print(f"JARVIS will now start when you log in.\n  {link}\nRemove it with --uninstall-autostart.")
        else:
            print("Removed from login startup." if autostart.uninstall() else "Was not set to start at login.")
        return

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_config(args.config)
    bus = B.Bus()
    confirm = ConfirmServer()
    brain = make_brain(cfg, confirm)
    sessions = SessionManager(cfg, brain, on_status=lambda m: bus.publish(B.NOTICE, text=m, level="info"))

    speaker = listener = None
    if not args.text and not args.no_voice:
        print("Loading voice models...")
        from .audio.stt import Listener
        from .audio.tts import Speaker
        v = cfg["voice"]
        speaker = Speaker(voice=v["tts_voice"], rate=v["tts_rate"])
        listener = Listener(model_size=v["whisper_model"], device=v["whisper_device"],
                            compute_type=v["whisper_compute_type"], language=v.get("language"))

    assistant = Assistant(cfg, brain, sessions, bus, speaker=speaker, listener=listener)
    confirm.asker = assistant.ask_confirm
    start_idle_watcher(sessions, brain, bus)

    if args.text:
        run_text(assistant, confirm)
        return

    # HUD
    from .ui_server import UIServer
    ui = UIServer(bus, port=0)
    ui.on_input = lambda t: threading.Thread(target=assistant.handle_input, args=(t, "hud"), daemon=True).start()
    ui.on_confirm = assistant.resolve_confirm
    ui.on_control = assistant.control
    ui.start()

    shutdown = threading.Event()
    assistant._shutdown = shutdown

    win = None
    if not args.no_window:
        from .window import open_window
        win = open_window(ui.url)
    else:
        print(f"HUD at {ui.url}")

    # kill-switch hotkey + voice
    try:
        import keyboard
        keyboard.add_hotkey(cfg["desktop"]["kill_switch_hotkey"], assistant.kill_switch)
    except Exception as e:
        print(f"{DIM}[kill-switch hotkey unavailable: {e}]{RESET}")

    voice = None
    if speaker and listener:
        from .voice_loop import VoiceLoop
        from .audio.wake import WakeWord
        v = cfg["voice"]
        voice = VoiceLoop(cfg, assistant, bus)
        voice.wake = WakeWord(model=v["wake_model"], threshold=float(v["wake_threshold"]),
                              hotkey=v["push_to_talk_hotkey"])
        voice.start()
    else:
        bus.set_state("idle")
        bus.publish(B.NOTICE, text=f"Good day, {cfg.get('address_as') or 'sir'}. Type a command below.", level="info")

    # optional startup brief (needs your connectors authorized)
    integ = cfg.get("integrations", {})
    if integ.get("startup_brief") and integ.get("use_my_connectors"):
        def brief():
            time.sleep(2)
            assistant.handle_input(
                "Give me a brief: any important unread email, what's on my calendar today, and "
                "Notion tasks due. Keep it to two or three spoken sentences. If a tool isn't "
                "available, skip that part quietly.", source="startup")
        threading.Thread(target=brief, daemon=True).start()

    # wait until the window closes or shutdown is requested
    def watch_window():
        if win:
            win.wait()
            shutdown.set()
    threading.Thread(target=watch_window, daemon=True).start()
    try:
        while not shutdown.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        if voice:
            voice.stop()
        if speaker:
            speaker.stop()
        ui.stop()
        if win:
            win.close()


if __name__ == "__main__":
    main()
