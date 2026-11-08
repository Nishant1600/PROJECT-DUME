"""Local commands handled without calling Claude (saves usage and is instant)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .brain.session import SessionManager


@dataclass
class Handled:
    reply: str
    quit: bool = False


_NEW = re.compile(r"^(?:/new|(?:start )?(?:a )?(?:new|fresh) (?:session|conversation|chat)|fresh start)\W*$", re.I)
_PROJECT = re.compile(r"^(?:/project\s+|(?:switch|change) (?:to )?(?:the )?project (?:to )?)(?P<name>.+?)\W*$", re.I)
_STATUS = re.compile(r"^(?:/status|(?:session )?status)\W*$", re.I)
_QUIT = re.compile(r"^(?:/quit|/exit|exit|quit|goodbye(?: jarvis)?|shut ?down jarvis)\W*$", re.I)
_REMEMBER = re.compile(r"^(?:/remember\s+|remember (?:that\s+)?)(?P<fact>.+)$", re.I)
_HELP = re.compile(r"^(?:/help|help)\W*$", re.I)

HELP = ("Commands: new session | switch project to <name or path> | status | "
        "remember <fact> | goodbye.  Anything else goes to Claude.")


def handle_local(text: str, sessions: SessionManager) -> Handled | None:
    t = text.strip()
    if _HELP.match(t):
        return Handled(HELP)
    if _QUIT.match(t):
        return Handled("Goodbye.", quit=True)
    if _NEW.match(t):
        sessions.new_session()
        return Handled("Fresh session started. I've kept a summary of what we were doing.")
    if m := _PROJECT.match(t):
        path = sessions.switch_project(m.group("name"))
        if path is None:
            return Handled(f"I couldn't find a project called {m.group('name')}. "
                           "Add it under 'projects' in config.yaml or say its full path.")
        return Handled(f"Switched to {path.name}.")
    if _STATUS.match(t):
        s = sessions.status()
        if not s["session_id"]:
            return Handled(f"No active session. Working folder: {s['project_dir']}.")
        return Handled(f"Session has {s['turns']} turns and about {s['context_tokens']:,} of "
                       f"{s['token_cap']:,} context tokens. Working folder: {s['project_dir']}.")
    if m := _REMEMBER.match(t):
        sessions.remember(m.group("fact"))
        return Handled("Noted. I'll remember that.")
    return None
