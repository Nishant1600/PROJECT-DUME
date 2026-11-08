"""Localhost endpoint the confirm MCP server calls; asks the user via the active asker."""
from __future__ import annotations

import json
import re
import secrets
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable


@dataclass
class Question:
    tool_name: str
    input: dict
    spoken: str     # short, speakable description
    detail: str     # full description for the screen


@dataclass
class Answer:
    allow: bool
    always: bool = False
    message: str = ""


Asker = Callable[[Question], Answer]


def describe_action(tool: str, inp: dict) -> tuple[str, str]:
    short = tool.split("__")[-1]
    if tool in ("Bash", "PowerShell"):
        cmd = str(inp.get("command", ""))
        why = inp.get("description")
        return (f"run a command to {why[0].lower() + why[1:]}" if why else "run a command",
                f"{tool}: {cmd}")
    if tool in ("Write", "Edit", "NotebookEdit"):
        path = str(inp.get("file_path") or inp.get("notebook_path") or "")
        verb = "write" if tool == "Write" else "edit"
        return f"{verb} the file {Path(path).name}", f"{tool}: {path}"
    if short == "open_app":
        target = inp.get("name_or_path") or inp.get("name") or ""
        return f"open {target}", f"open_app: {target}"
    if short == "click_element":
        return f"click {inp.get('name')}", f"click_element: {json.dumps(inp)}"
    if short == "click":
        return f"click at {inp.get('x')}, {inp.get('y')}", f"click: {json.dumps(inp)}"
    if short == "type_text":
        text = str(inp.get("text", ""))
        return f"type: {text[:80]}", f"type_text: {text}"
    if short == "hotkey":
        return f"press {inp.get('keys')}", f"hotkey: {inp.get('keys')}"
    if short == "focus_window":
        return f"switch to {inp.get('title_contains')}", f"focus_window: {json.dumps(inp)}"
    if short == "scroll":
        return "scroll the page", f"scroll: {json.dumps(inp)}"
    return f"use the {short.replace('_', ' ')} tool", f"{tool}: {json.dumps(inp)[:400]}"


_YES = re.compile(r"\b(yes|yeah|yep|yup|sure|okay|ok|go ahead|do it|proceed|confirm(ed)?|affirmative|haan)\b", re.I)
_ALWAYS = re.compile(r"\b(always|don'?t ask|every time|for (this|the) session)\b", re.I)
_NO = re.compile(r"\b(no|nope|don'?t|stop|cancel|wait|nahi)\b", re.I)


def parse_answer(text: str) -> Answer:
    t = text.strip().lower()
    if t in ("y", "a"):
        return Answer(True, always=(t == "a"))
    if _ALWAYS.search(t):
        return Answer(True, always=True)
    if _YES.search(t) and not _NO.search(t):
        return Answer(True)
    return Answer(False, message=(
        f"The user declined this action (they said: {text.strip()!r}). Do not try to do the same "
        "thing another way. Stop and briefly ask the user what they want instead."))


class ConfirmServer:
    def __init__(self, asker: Asker | None = None):
        self.token = secrets.token_hex(16)
        self.asker: Asker | None = asker
        self.always: set[str] = set()
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep the console quiet
                pass

            def do_POST(self):
                if self.path != "/confirm" or self.headers.get("X-Jarvis-Token") != outer.token:
                    self.send_response(403)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    data = json.loads(self.rfile.read(length) or b"{}")
                    ans = outer.decide(str(data.get("tool_name", "?")), data.get("input") or {})
                except Exception as e:
                    ans = Answer(False, message=f"Confirmation failed: {e}")
                body = json.dumps({"allow": ans.allow, "message": ans.message}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def decide(self, tool_name: str, inp: dict) -> Answer:
        # Policy decides: BLOCK is refused even if the user says yes; ALLOW runs
        # without asking (full terminal access); only CONFIRM reaches the human.
        try:
            from .policy import ALLOW, BLOCK, get_policy

            decision = get_policy().classify(tool_name, inp)
            if decision.action == BLOCK:
                return Answer(False, message=(
                    f"BLOCKED by JARVIS safety policy ({decision.rule}): {decision.reason} "
                    "Do not attempt this or any equivalent command. Tell the user it is blocked."))
            if decision.action == ALLOW:
                return Answer(True)
        except Exception:
            pass
        if tool_name in self.always:
            return Answer(True)
        with self._lock:  # one question at a time
            if self.asker is None:
                return Answer(False, message="Nobody is available to confirm this action.")
            spoken, detail = describe_action(tool_name, inp)
            ans = self.asker(Question(tool_name, inp, spoken, detail))
        if ans.allow and ans.always:
            self.always.add(tool_name)
        return ans
