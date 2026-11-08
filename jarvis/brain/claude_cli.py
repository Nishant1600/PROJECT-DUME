"""Run turns of the Claude Code CLI in headless mode (`claude -p`) and stream the output.

Uses the user's Claude subscription login. Each turn spawns `claude -p`, continuing the
conversation with `--resume <session_id>`. Flags keep the prompt lean (only our MCP
servers, no skills, a short tool list) so each turn uses as little of the plan as possible.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

TextCallback = Callable[[str], None]
ToolCallback = Callable[[str, dict], None]


@dataclass
class TurnResult:
    text: str = ""
    session_id: str | None = None
    context_tokens: int = 0          # size of the conversation at the last model call
    cost_usd: float | None = None    # what the turn would cost on the API (informational)
    is_error: bool = False
    error: str | None = None
    cancelled: bool = False
    tools_used: list[str] = field(default_factory=list)


class ClaudeCLI:
    def __init__(
        self,
        cfg: dict,
        persona_file: Path,
        mcp_config: Path | None = None,
        permission_prompt_tool: str | None = None,
        extra_system: str = "",
    ):
        self.ccfg = cfg["claude"]
        base = persona_file.read_text(encoding="utf-8") if persona_file.exists() else ""
        if cfg.get("address_as"):
            base += f"\nAddress the user as {cfg['address_as']}.\n"
        self.persona = base
        # extra_system may be a string, or a callable re-evaluated every turn so that
        # live edits to the rulebook / learned profile take effect without a restart.
        self._extra = extra_system
        self.mcp_config = mcp_config
        self.permission_prompt_tool = permission_prompt_tool
        self.exe = shutil.which(self.ccfg["command"]) or self.ccfg["command"]
        self._proc: subprocess.Popen | None = None
        self._cancelled = False

    def _persona_text(self) -> str:
        """Base persona + the live extra (identity, rulebook, learned patterns), re-read each turn."""
        extra = self._extra() if callable(self._extra) else (self._extra or "")
        if extra and self.persona:
            return self.persona + "\n" + extra
        return extra or self.persona

    # ------------------------------------------------------------------ args
    def _args(self, *, session_id: str | None, model: str | None, tools: str,
              allowed: list[str], persona: bool, mcp: bool, persist: bool) -> list[str]:
        args = [
            self.exe, "-p",
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--disable-slash-commands",     # no skill listings in the prompt
            "--tools", tools,
        ]
        if not self.ccfg.get("integrations", {}).get("use_my_connectors"):
            args.append("--strict-mcp-config")   # only our MCP servers, not every plugin's
        if allowed:
            args += ["--allowedTools", ",".join(allowed)]
        if mcp and self.mcp_config:
            args += ["--mcp-config", str(self.mcp_config)]
            if self.permission_prompt_tool:
                args += ["--permission-prompt-tool", self.permission_prompt_tool]
        if persona:
            ptext = self._persona_text()
            if ptext:
                args += ["--append-system-prompt", ptext]
        if model:
            args += ["--model", model]
        if self.ccfg.get("effort"):
            args += ["--effort", str(self.ccfg["effort"])]
        if not persist:
            args.append("--no-session-persistence")
        if session_id:
            args += ["--resume", session_id]
        args += [str(a) for a in self.ccfg.get("extra_args") or []]
        return args

    # ------------------------------------------------------------------ run
    def run_turn(self, prompt: str, cwd: Path, session_id: str | None = None,
                 on_text: TextCallback | None = None, on_tool: ToolCallback | None = None) -> TurnResult:
        """One user turn in the JARVIS conversation (persona, tools, MCP, persisted session)."""
        allowed = list(self.ccfg.get("allowed_tools") or [])
        if self.ccfg.get("auto_approve_desktop_actions"):
            allowed.append("mcp__jarvis-desktop")
        args = self._args(session_id=session_id, model=self.ccfg.get("model"),
                          tools=self.ccfg.get("tools") or "default", allowed=allowed,
                          persona=True, mcp=True, persist=True)
        return self._run(args, prompt, cwd, on_text, on_tool,
                         timeout=float(self.ccfg.get("turn_timeout_seconds") or 600))

    def oneshot(self, prompt: str, cwd: Path, model: str | None = None, timeout: float = 120) -> TurnResult:
        """A tool-less, unsaved call (used for writing session summaries)."""
        args = self._args(session_id=None, model=model, tools="", allowed=[],
                          persona=False, mcp=False, persist=False)
        return self._run(args, prompt, cwd, None, None, timeout=timeout)

    def cancel(self) -> None:
        """Kill the running turn (kill switch / "stop")."""
        self._cancelled = True
        proc = self._proc
        if proc and proc.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True)
            else:
                proc.kill()

    @property
    def busy(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _run(self, args: list[str], prompt: str, cwd: Path, on_text: TextCallback | None,
             on_tool: ToolCallback | None, timeout: float) -> TurnResult:
        res = TurnResult()
        self._cancelled = False
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(args, cwd=str(cwd), stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    creationflags=flags)
        except OSError as e:
            res.is_error, res.error = True, f"could not start Claude CLI: {e}"
            return res
        self._proc = proc

        stderr_chunks: list[bytes] = []
        t_err = threading.Thread(target=lambda: stderr_chunks.append(proc.stderr.read()), daemon=True)
        t_err.start()
        timer = threading.Timer(timeout, self.cancel)
        timer.start()

        try:
            proc.stdin.write(prompt.encode("utf-8"))
            proc.stdin.close()
        except OSError:
            pass

        streamed_ids: set[str] = set()   # messages whose text arrived as deltas
        current_id: str | None = None
        emitted_any = False

        def emit(text: str) -> None:
            nonlocal emitted_any
            if text and on_text:
                on_text(text)
            emitted_any = emitted_any or bool(text)

        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = ev.get("type")
                if kind == "system" and ev.get("subtype") == "init":
                    res.session_id = ev.get("session_id") or res.session_id
                elif kind == "stream_event":
                    se = ev.get("event", {})
                    st = se.get("type")
                    if st == "message_start":
                        current_id = se.get("message", {}).get("id")
                        if emitted_any:
                            emit("\n")   # separate text before/after tool calls
                    elif st == "content_block_delta":
                        delta = se.get("delta", {})
                        if delta.get("type") == "text_delta":
                            if current_id:
                                streamed_ids.add(current_id)
                            emit(delta.get("text", ""))
                elif kind == "assistant":
                    msg = ev.get("message", {})
                    usage = msg.get("usage") or {}
                    ctx = (usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                           + usage.get("cache_creation_input_tokens", 0))
                    if ctx:
                        res.context_tokens = ctx
                    for block in msg.get("content", []):
                        btype = block.get("type")
                        if btype == "text" and msg.get("id") not in streamed_ids:
                            emit(block.get("text", ""))
                        elif btype == "tool_use":
                            name = block.get("name", "?")
                            res.tools_used.append(name)
                            if on_tool:
                                on_tool(name, block.get("input") or {})
                elif kind == "result":
                    res.session_id = ev.get("session_id") or res.session_id
                    res.text = ev.get("result") or ""
                    res.cost_usd = ev.get("total_cost_usd")
                    if ev.get("is_error") or str(ev.get("subtype", "")).startswith("error"):
                        res.is_error = True
                        res.error = res.text or ev.get("subtype")
        except BaseException:
            self.cancel()   # e.g. Ctrl+C: don't leave the CLI running
            raise
        finally:
            proc.wait()
            timer.cancel()
            t_err.join(timeout=2)
            self._proc = None

        res.cancelled = self._cancelled
        if proc.returncode not in (0, None) and not res.text and not res.cancelled:
            res.is_error = True
            err = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
            res.error = res.error or (err[-800:] if err else f"Claude CLI exited with {proc.returncode}")
        return res
