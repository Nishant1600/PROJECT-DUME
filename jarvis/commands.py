"""Local commands handled without calling Claude (saves usage and is instant)."""
from __future__ import annotations

import re
from dataclasses import dataclass

import datetime

from .brain.session import SessionManager
from .nudges import Reminders, parse_when
from .routines import DELETE_RE, LIST_RE, SAVE_RE, Routines

_REMIND = re.compile(r"^(?:/remind\s+|remind me(?:\s+to)?\s+)(?P<body>.+)$", re.I)
_REMINDERS_LIST = re.compile(r"^(?:list|show|what are)\s+(?:my\s+)?reminders\W*$", re.I)


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
    # routines (#18): saving / forgetting / listing is local; triggering is handled upstream
    routines = Routines()
    if m := SAVE_RE.match(t):
        name = routines.save(m.group("name"), m.group("prompt"))
        if name:
            return Handled(f"Saved the {name} routine. Say \"run {name}\" to use it.")
        return Handled("I couldn't save that routine - give it a name and what it should do.")
    if m := DELETE_RE.match(t):
        ok = routines.delete(m.group("name"))
        return Handled(f"Forgot the {m.group('name')} routine." if ok
                       else f"I don't have a routine called {m.group('name')}.")
    if LIST_RE.match(t):
        names = routines.names()
        return Handled("Your routines: " + ", ".join(names) + "." if names
                       else "You haven't saved any routines yet.")
    # reminders (#16): set by the user, fired proactively by the nudge engine
    if m := _REMIND.match(t):
        parsed = parse_when(m.group("body"))
        if not parsed:
            return Handled("When should I remind you? Try \"in 10 minutes\" or \"at 6pm\".")
        what, ts = parsed
        if not what:
            return Handled("What should I remind you about?")
        Reminders().add(what, ts)
        when = datetime.datetime.fromtimestamp(ts).strftime("%I:%M %p").lstrip("0")
        return Handled(f"I'll remind you to {what} at {when}.")
    if _REMINDERS_LIST.match(t):
        items = Reminders().all()
        if not items:
            return Handled("You have no reminders set.")
        parts = [f"{r['text']} at {datetime.datetime.fromtimestamp(r['ts']).strftime('%I:%M %p').lstrip('0')}"
                 for r in items]
        return Handled("Reminders: " + "; ".join(parts) + ".")
    return None
