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
from .nudges import NudgeEngine
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
                      extra_system=profile.persona_addendum)  # callable: re-read live each turn
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
        from . import notetaker
        if notetaker.is_start_trigger(line):
            notetaker.start(); print('jarvis> Taking notes. Say "stop taking notes" to finish.'); continue
        if notetaker.is_stop_trigger(line):
            path = notetaker.stop()
            if not path:
                print("jarvis> I wasn't taking notes."); continue
            line = notetaker.summary_prompt(path, assistant.cfg)
        else:
            notetaker.feed(line)
            from .brief import brief_prompt, is_brief_trigger, is_status_trigger, status_prompt
            from .followup import is_followup, resolve_now
            if is_followup(line) and resolve_now(line):
                line = resolve_now(line)[1]
            elif is_brief_trigger(line):
                line = brief_prompt(assistant.cfg)
            elif is_status_trigger(line):
                line = status_prompt(assistant.cfg)
            else:
                from .clipboard import build_prompt, get_text, is_trigger
                from .diffreview import is_trigger as is_diff_trigger, prompt as diff_prompt
                from .tutor import is_trigger as tutor_t, topic_of as tutor_topic, tutor_prompt
                from .revision import is_trigger as rev_t, topic_of as rev_topic, revision_prompt
                from .recap import is_trigger as recap_t, span_of, recap_prompt
                from .scheduling import is_trigger as sch_t, scheduling_prompt
                from .prflow import is_trigger as pr_t, pr_prompt
                from . import components as _comp
                if is_diff_trigger(line):
                    line = diff_prompt(str(assistant.sessions.cwd))
                elif is_trigger(line):
                    clip = get_text()
                    if not clip:
                        print("jarvis> There's nothing text-like on your clipboard."); continue
                    line = build_prompt(line, clip)
                elif tutor_t(line):
                    line = tutor_prompt(tutor_topic(line), assistant.cfg)
                elif rev_t(line):
                    line = revision_prompt(assistant.cfg, rev_topic(line))
                elif recap_t(line):
                    line = recap_prompt(assistant.cfg, span_of(line))
                elif sch_t(line):
                    line = scheduling_prompt(line, assistant.cfg)
                elif pr_t(line):
                    line = pr_prompt(str(assistant.sessions.cwd), assistant.cfg)
                elif _comp.use_name(line):
                    code = _comp.get(_comp.use_name(line))
                    if not code:
                        print(f"jarvis> No saved component called {_comp.use_name(line)}."); continue
                    line = _comp.use_prompt(_comp.use_name(line), code, line)
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
    ap.add_argument("--tray", action="store_true", help="run in the system tray; open the HUD on demand")
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
        speaker = Speaker(voice=v["tts_voice"], rate=v["tts_rate"],
                          piper_exe=v.get("piper_exe"), piper_model=v.get("piper_model"))
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

    def show_hud():
        nonlocal win
        if win is None:
            from .window import open_window
            win = open_window(ui.url)
        return win

    tray = None
    if args.tray:
        from . import tray as tray_mod
        if tray_mod.available():
            tray = tray_mod.TrayPresence(bus, on_show=show_hud,
                                         on_new_session=sessions.new_session,
                                         on_quit=shutdown.set).start()
            print(f"JARVIS is in the system tray. HUD on demand at {ui.url}")
        else:
            print("[tray needs: pip install pystray pillow - continuing without it]")

    if not args.no_window and not args.tray:
        show_hud()                      # normal mode: open the HUD now
    elif args.no_window and not args.tray:
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
        from .brief import brief_prompt
        def brief():
            time.sleep(2)
            assistant.handle_input(brief_prompt(cfg), source="startup")
        threading.Thread(target=brief, daemon=True).start()

    # proactive nudges + reminders (#16): idle-only, rate-limited; nudges opt-in via config
    _idle = lambda: (not brain.busy and bus.state == "idle"
                     and not (speaker and speaker.speaking))
    nudges = NudgeEngine(
        cfg, bus, speaker, sessions, is_idle=_idle,
        address_as=cfg.get("address_as") or "sir").start()

    # scheduled routines (#54): fire "every weekday at 9, brief me" style schedules when due
    from .schedules import ScheduleEngine
    schedules_engine = ScheduleEngine(
        bus, run=lambda instr: assistant.handle_input(instr, source="schedule"),
        is_idle=_idle).start()

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
        if nudges:
            nudges.stop()
        if schedules_engine:
            schedules_engine.stop()
        if speaker:
            speaker.stop()
        if tray:
            tray.stop()
        ui.stop()
        if win:
            win.close()


if __name__ == "__main__":
    main()
