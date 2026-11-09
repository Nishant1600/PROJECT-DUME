"""Local commands handled without calling Claude (saves usage and is instant)."""
from __future__ import annotations

import re
from dataclasses import dataclass

import datetime

from .brain.session import SessionManager
from .deadlines import Deadlines, parse_due
from .nudges import Reminders, parse_when
from .routines import DELETE_RE, LIST_RE, SAVE_RE, Routines

_REMIND = re.compile(r"^(?:/remind\s+|remind me(?:\s+to)?\s+)(?P<body>.+)$", re.I)
_REMINDERS_LIST = re.compile(r"^(?:list|show|what are)\s+(?:my\s+)?reminders\W*$", re.I)
_DEADLINE_ADD = re.compile(r"^(?:/deadline\s+|(?:add|track|set)(?:\s+a)?\s+deadline\b[:,]?\s*)(?P<body>.+)$", re.I)
_DEADLINE_DUE = re.compile(r"^(?:what(?:'s| is)?\s+due|what\s+(?:are\s+)?my\s+deadlines|anything\s+due|deadlines)\b.*$", re.I)
_DEADLINE_DROP = re.compile(r"^(?:/undeadline\s+|(?:remove|delete|drop|clear)\s+(?:the\s+)?deadline\b[:,]?\s*)(?P<ident>.+)$", re.I)
_FOCUS_ON = re.compile(r"^(?:/focus|focus(?:\s+mode)?(?:\s+on)?|do not disturb|dnd|quiet mode|hush|heads down)\W*$", re.I)
_FOCUS_OFF = re.compile(r"^(?:/unfocus|focus\s+off|unfocus|stop\s+focus|exit\s+focus|end\s+focus|i'?m\s+back|resume\s+nudges|normal\s+mode)\W*$", re.I)


@dataclass
class Handled:
    reply: str
    quit: bool = False


_NEW = re.compile(r"^(?:/new|(?:start )?(?:a )?(?:new|fresh) (?:session|conversation|chat)|fresh start)\W*$", re.I)
_PROJECT = re.compile(r"^(?:/project\s+|(?:switch|change) (?:to )?(?:the )?project (?:to )?)(?P<name>.+?)\W*$", re.I)
_STATUS = re.compile(r"^(?:/status|session status|session info)\W*$", re.I)
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
    # usage radar: trend today + how close this session is to rotating (local, no Claude call)
    from .usage import is_trigger as _usage_trigger, spoken as _usage_spoken
    if _usage_trigger(t):
        return Handled(_usage_spoken(sessions.status(), sessions.cfg))
    # self health-check (#52): mic, TTS, GPU, CLI, connectors, disk — local, instant
    from .healthcheck import is_trigger as _health_trigger, run_checks as _run_health
    if _health_trigger(t):
        return Handled(_run_health(sessions.cfg)["summary"])
    # agenda board (#58): a glanceable spoken view of deadlines, schedules and reminders
    from .agenda import is_trigger as _agenda_trigger, board as _board, spoken as _board_spoken
    if _agenda_trigger(t):
        return Handled(_board_spoken(_board(sessions.cfg)))
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
    # deadline tracker: adding/removing/listing is local and instant (no model call)
    if m := _DEADLINE_DROP.match(t):
        ident = m.group("ident").strip(" .")
        return Handled("Removed that deadline." if Deadlines().remove(ident)
                       else f"I'm not tracking a deadline matching {ident}.")
    if m := _DEADLINE_ADD.match(t):
        parsed = parse_due(m.group("body"))
        if not parsed:
            return Handled("When is it due? Try \"submit the report by Friday\" or \"...by 25 Nov\".")
        title, due = parsed
        if not title:
            return Handled("What's the deadline for? Tell me what's due and when.")
        Deadlines().add(title, due)
        return Handled(f"Tracking that — {title}, due {due.strftime('%A %d %B')}.")
    if _DEADLINE_DUE.match(t):
        items = Deadlines().due(within_days=14)
        if not items:
            return Handled("Nothing due in the next two weeks.")
        parts = [f"{it['title']} ({it['when']})" for it in items[:6]]
        return Handled("Coming up: " + "; ".join(parts) + ".")
    # focus mode: hush unsolicited nudges while you work (reminders are held, not lost)
    if _FOCUS_OFF.match(t):
        from . import focus
        focus.off()
        return Handled("Focus mode off. Anything I was holding will come through now.")
    if _FOCUS_ON.match(t):
        from . import focus
        focus.on()
        return Handled("Focus mode on. I'll stay quiet until you say you're back.")
    # scheduled routines (#54): "brief me every weekday at 9am" / "schedule X every day at 8"
    from . import schedules as _sched
    if _sched.LIST_RE.match(t):
        items = _sched.Schedules().all()
        if not items:
            return Handled("You have no schedules set.")
        parts = [f"{it['instruction']} {_sched.describe(it['spec'])}" for it in items[:6]]
        return Handled("Scheduled: " + "; ".join(parts) + ".")
    if m := _sched.DELETE_RE.match(t):
        ident = m.group("ident").strip(" .")
        return Handled("Removed that schedule." if _sched.Schedules().remove(ident)
                       else f"I'm not running a schedule matching {ident}.")
    _sched_body = None
    if m := _sched.SAVE_RE.match(t):
        _sched_body = m.group("body")
    elif _sched.has_schedule_keyword(t):
        parsed = _sched.parse_schedule(t)
        if parsed and parsed[0]:
            _sched_body = t
    if _sched_body is not None:
        parsed = _sched.parse_schedule(_sched_body)
        if not parsed:
            return Handled("When should it run? Try \"brief me every weekday at 9am\".")
        instr, spec = parsed
        if not instr:
            return Handled("What should I run on that schedule?")
        _sched.Schedules().add(instr, spec)
        return Handled(f"Scheduled: {instr} — {_sched.describe(spec)}.")
    return None
