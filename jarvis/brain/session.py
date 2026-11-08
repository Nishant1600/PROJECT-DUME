"""Conversation sessions: resume, rotate, summarize, remember.

A session is rotated (a fresh `claude -p` conversation is started) when
  * nobody has spoken for `idle_minutes`, or
  * the conversation context has grown past `token_cap` tokens,
because cost grows with how much context is re-sent each turn, not with age.
Before rotating, a cheap model writes a short summary of the user/assistant text
(tool output is never kept), and the next session starts with that summary plus
the saved facts, so JARVIS keeps track of what you were doing.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..config import MEMORY_DIR, RUNTIME_DIR, resolve_path
from .claude_cli import ClaudeCLI, TextCallback, ToolCallback, TurnResult

REMEMBER_RE = re.compile(r"\[\[REMEMBER:\s*(.+?)\]\]", re.S)
StatusCallback = Callable[[str], None]


class SessionManager:
    def __init__(self, cfg: dict, brain: ClaudeCLI, on_status: StatusCallback | None = None):
        self.cfg = cfg
        self.scfg = cfg["session"]
        self.brain = brain
        self.on_status = on_status or (lambda msg: None)
        self.state_file = RUNTIME_DIR / "session.json"
        self.transcript_file = RUNTIME_DIR / "transcript.jsonl"
        self.summary_file = MEMORY_DIR / "summary.md"
        self.facts_file = MEMORY_DIR / "facts.md"
        from ..profile import Profile
        self.profile = Profile(cfg)   # identity + learned working patterns
        self._lock = threading.RLock()
        self.state = self._load_state()

    # ------------------------------------------------------------ state
    def _load_state(self) -> dict:
        default = {"session_id": None, "last_activity": 0.0, "context_tokens": 0,
                   "cwd": str(resolve_path(self.cfg["project_dir"])), "turns": 0}
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            default.update(data)
        except (OSError, ValueError):
            pass
        return default

    def _save_state(self) -> None:
        self.state_file.write_text(json.dumps(self.state, indent=2), encoding="utf-8")

    @property
    def cwd(self) -> Path:
        return Path(self.state["cwd"])

    def status(self) -> dict:
        idle = time.time() - self.state["last_activity"] if self.state["last_activity"] else None
        return {
            "session_id": self.state["session_id"],
            "turns": self.state["turns"],
            "context_tokens": self.state["context_tokens"],
            "token_cap": self.scfg["token_cap"],
            "idle_minutes": round(idle / 60, 1) if idle is not None else None,
            "project_dir": self.state["cwd"],
        }

    # ------------------------------------------------------------ rotation
    def rotation_reason(self) -> str | None:
        if not self.state["session_id"]:
            return None
        idle = time.time() - float(self.state["last_activity"] or 0)
        if idle > float(self.scfg["idle_minutes"]) * 60:
            return f"idle for {idle / 60:.0f} minutes"
        if int(self.state["context_tokens"]) > int(self.scfg["token_cap"]):
            return f"context reached {self.state['context_tokens']} tokens"
        return None

    def maybe_rotate(self) -> bool:
        """Call periodically (e.g. every 30 s) so rotation happens before the next question."""
        with self._lock:
            reason = self.rotation_reason()
            if reason:
                self.end_session(reason)
                return True
        return False

    def end_session(self, reason: str) -> None:
        with self._lock:
            if not self.state["session_id"] and not self.transcript_file.exists():
                return
            self.on_status(f"Starting a fresh session ({reason}); summarizing the last one...")
            self._summarize()
            self.transcript_file.unlink(missing_ok=True)
            self.state.update(session_id=None, context_tokens=0, turns=0)
            self._save_state()

    def new_session(self) -> None:
        self.end_session("you asked for a new session")

    def switch_project(self, name_or_path: str) -> Path | None:
        projects = {k.lower(): v for k, v in (self.cfg.get("projects") or {}).items()}
        target = projects.get(name_or_path.strip().lower())
        path = resolve_path(target) if target else Path(name_or_path).expanduser()
        if not path.is_dir():
            return None
        # Claude Code stores sessions per folder, so a new folder means a new session.
        self.end_session(f"switching project to {path.name}")
        with self._lock:
            self.state["cwd"] = str(path.resolve())
            self._save_state()
        return path

    def _transcript_text(self, max_chars: int = 16000) -> str:
        if not self.transcript_file.exists():
            return ""
        lines = []
        for raw in self.transcript_file.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(raw)
            except ValueError:
                continue
            who = "User" if item.get("role") == "user" else "JARVIS"
            lines.append(f"{who}: {item.get('text', '').strip()}")
        text = "\n".join(lines)
        return text[-max_chars:]

    def _summarize(self) -> None:
        transcript = self._transcript_text()
        if not transcript.strip():
            return
        previous = self.summary_file.read_text(encoding="utf-8").strip() if self.summary_file.exists() else ""
        words = int(self.scfg.get("summary_words", 150))
        prompt = (
            f"You maintain the running memory of a voice assistant called JARVIS. Write an updated "
            f"summary in at most {words} words of plain prose (no headings) covering: what the user "
            f"is working on, decisions made, unfinished tasks, and specifics worth keeping (file "
            f"paths, names, numbers). Merge the previous summary with the new conversation; drop "
            f"anything stale or trivial. Output only the summary.\n\n"
            f"<previous_summary>\n{previous or '(none)'}\n</previous_summary>\n\n"
            f"<conversation>\n{transcript}\n</conversation>"
        )
        res = self.brain.oneshot(prompt, cwd=RUNTIME_DIR, model=self.cfg["claude"].get("summary_model"))
        if res.is_error or not res.text.strip():
            # Keep the raw tail rather than lose context.
            fallback = (previous + "\n\nRecent conversation (unsummarized):\n" + transcript[-1500:]).strip()
            self.summary_file.write_text(fallback, encoding="utf-8")
            self.on_status(f"Summary failed ({res.error}); kept the raw transcript tail.")
            return
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        summary = res.text.strip()
        self.summary_file.write_text(f"(updated {stamp})\n{summary}\n", encoding="utf-8")
        # Learn durable working patterns from this conversation (background, cheap model).
        try:
            if self.profile.learn(summary, self.brain, model=self.cfg["claude"].get("summary_model")):
                self.on_status("Updated what I know about how you work.")
        except Exception:
            pass

    def context_block(self) -> str:
        recent = []
        if self.summary_file.exists() and self.summary_file.read_text(encoding="utf-8").strip():
            recent.append("<recently>\n" + self.summary_file.read_text(encoding="utf-8").strip() + "\n</recently>")
        if self.facts_file.exists() and self.facts_file.read_text(encoding="utf-8").strip():
            recent.append("<facts you told me>\n" + self.facts_file.read_text(encoding="utf-8").strip() + "\n</facts you told me>")
        # identity + learned profile first, then recent summary/facts
        return self.profile.context_block(extra="\n".join(recent))

    # ------------------------------------------------------------ memory
    def remember(self, fact: str) -> None:
        fact = " ".join(fact.split())
        if not fact:
            return
        existing = self.facts_file.read_text(encoding="utf-8") if self.facts_file.exists() else ""
        if fact.lower() in existing.lower():
            return
        with open(self.facts_file, "a", encoding="utf-8") as f:
            f.write(f"- {fact} ({datetime.now():%Y-%m-%d})\n")

    def _append_transcript(self, role: str, text: str) -> None:
        if not text.strip():
            return
        with open(self.transcript_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"role": role, "text": text, "ts": time.time()}) + "\n")

    # ------------------------------------------------------------ turns
    def ask(self, text: str, on_text: TextCallback | None = None,
            on_tool: ToolCallback | None = None) -> TurnResult:
        with self._lock:
            reason = self.rotation_reason()
            if reason:
                self.end_session(reason)
            res = self._turn(text, on_text, on_tool)
            if (res.is_error and self.state["session_id"] and not res.text and not res.cancelled):
                # Most often the saved session can't be resumed any more; start fresh once.
                self.on_status(f"Couldn't continue the session ({res.error}); starting a new one.")
                self.state["session_id"] = None
                res = self._turn(text, on_text, on_tool)

            if res.session_id and not res.is_error:
                self.state["session_id"] = res.session_id
            if res.context_tokens:
                self.state["context_tokens"] = res.context_tokens
            self.state["last_activity"] = time.time()
            self.state["turns"] += 1

            for fact in REMEMBER_RE.findall(res.text or ""):
                self.remember(fact)
            self._append_transcript("user", text)
            self._append_transcript("assistant", REMEMBER_RE.sub("", res.text or "").strip())
            self._save_state()

        if int(self.state["context_tokens"]) > int(self.scfg["token_cap"]):
            # Rotate in the background so the next question doesn't wait for the summary.
            threading.Thread(target=self.maybe_rotate, daemon=True).start()
        return res

    def _turn(self, text: str, on_text, on_tool) -> TurnResult:
        prompt = text
        if not self.state["session_id"]:
            prompt = self.context_block() + text
        return self.brain.run_turn(prompt, self.cwd, self.state["session_id"], on_text, on_tool)
