"""The orchestrator: ties the brain, sessions, voice, policy, HUD bus and confirmations together.

Both the microphone (voice_loop) and the HUD's text box feed handle_input(). Confirmations are
shown on the HUD *and* asked by voice at the same time; whichever answers first wins. All visible
state goes onto the bus so the HUD mirrors exactly what JARVIS is doing.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass

from . import bus as B
from .brain.claude_cli import ClaudeCLI
from .brain.session import SessionManager
from .commands import handle_local
from .confirm_server import Answer, Question, describe_action, parse_answer
from .config import STOP_FILE
from .policy import CONFIRM, get_policy
from .speech_text import SentenceSplitter, TagStripper


@dataclass
class _Pending:
    id: str
    event: threading.Event
    answer: Answer | None = None


class Assistant:
    def __init__(self, cfg: dict, brain: ClaudeCLI, sessions: SessionManager, bus: B.Bus,
                 speaker=None, listener=None):
        self.cfg = cfg
        self.brain = brain
        self.sessions = sessions
        self.bus = bus
        self.speaker = speaker          # Speaker or None (text mode)
        self.listener = listener        # Listener or None
        self.policy = get_policy()
        from .routines import Routines
        self.routines = Routines()      # #18: named routines triggered by phrase
        self._busy = threading.Lock()   # one turn at a time
        self._pending: _Pending | None = None
        self._pending_lock = threading.Lock()
        self.interrupted = threading.Event()
        self._confirm_counter = 0
        self._last_reply = ""            # last spoken answer (for "send that to my phone")

    # ----------------------------------------------------------------- output
    def _emit_session(self) -> None:
        s = self.sessions.status()
        self.bus.publish(B.SESSION, turns=s["turns"], context_tokens=s["context_tokens"],
                         token_cap=s["token_cap"], project=s["project_dir"].replace("\\", "/").split("/")[-1])

    def speak(self, text: str) -> None:
        if self.speaker and text and not self.interrupted.is_set():
            self.speaker.say(text)

    # ----------------------------------------------------------------- confirm
    def ask_confirm(self, q: Question) -> Answer:
        """Called by the confirm channel for every non-pre-approved tool. The policy decides:
        ALLOW runs silently, BLOCK is refused, only CONFIRM actually asks you."""
        from .policy import ALLOW, BLOCK, CONFIRM as C_CONFIRM
        decision = self.policy.classify(q.tool_name, q.input)
        if decision.action == BLOCK:
            self.bus.publish(B.NOTICE, text=f"Blocked: {decision.rule}", level="error")
            return Answer(False, message=(
                f"BLOCKED by JARVIS safety policy ({decision.rule}): {decision.reason} "
                "Do not attempt this or any equivalent command. Tell the user it is blocked."))
        if decision.action == ALLOW:
            return Answer(True)
        self._confirm_counter += 1
        cid = f"c{self._confirm_counter}"
        pending = _Pending(cid, threading.Event())
        with self._pending_lock:
            self._pending = pending
        self.bus.set_state("acting", "waiting for your go-ahead")
        self.bus.publish(B.CONFIRM, id=cid, tool=q.tool_name, spoken=q.spoken, detail=q.detail)

        voice_thread = None
        if self.speaker and self.listener:
            voice_thread = threading.Thread(target=self._confirm_by_voice, args=(pending, q), daemon=True)
            voice_thread.start()

        pending.event.wait(timeout=120)
        with self._pending_lock:
            self._pending = None
        ans = pending.answer or Answer(False, message="No answer was given, so the action was not run.")
        self.bus.publish(B.CONFIRM_RESULT, id=cid, allow=ans.allow)
        self.bus.publish(B.NOTICE, text=("Approved" if ans.allow else "Declined") + f": {q.spoken}",
                         level="info" if ans.allow else "warn")
        if self.speaker:
            try:
                self.listener and self.listener.cancel()
            except Exception:
                pass
            self.speaker.say("Okay." if ans.allow else "Alright, I won't.")
        return ans

    def _confirm_by_voice(self, pending: _Pending, q: Question) -> None:
        try:
            self.speaker.wait(timeout=10)
            self.speaker.say(f"Shall I {q.spoken}?")
            self.speaker.wait(timeout=10)
            if pending.event.is_set():
                return
            reply = self.listener.listen(max_seconds=8, silence_ms=700, start_timeout=10)
            if pending.event.is_set() or not reply.strip():
                return
            self.bus.say_line("user", reply)
            self._resolve(pending.id, parse_answer(reply).allow, source="voice")
        except Exception:
            pass

    def resolve_confirm(self, cid: str, allow: bool) -> None:
        """Called by the HUD's /confirm POST."""
        self._resolve(cid, allow, source="hud")

    def _resolve(self, cid: str, allow: bool, source: str) -> None:
        with self._pending_lock:
            p = self._pending
            if not p or p.id != cid or p.event.is_set():
                return
            p.answer = Answer(allow, message="" if allow else
                              "The user declined this action. Do not try another way; ask them what to do.")
            p.event.set()

    # ----------------------------------------------------------------- input
    def handle_input(self, text: str, source: str = "voice") -> None:
        text = (text or "").strip()
        if not text:
            return
        if not self._busy.acquire(blocking=False):
            # A turn is already running: treat a new utterance as an interruption.
            self.interrupted.set()
            if self.speaker:
                self.speaker.stop()
            self.brain.cancel()
            self._busy.acquire()
        try:
            self._run_turn(text, source)
        finally:
            self._busy.release()

    def _run_turn(self, text: str, source: str) -> None:
        self.interrupted.clear()
        STOP_FILE.unlink(missing_ok=True)
        self.bus.say_line("user", text)
        original = text          # keep the raw utterance (for the offline screen-sense path)

        local = handle_local(text, self.sessions)
        if local:
            self.bus.say_line("jarvis", local.reply)
            self.speak(local.reply)
            self._last_reply = local.reply
            self._emit_session()
            self.bus.set_state("idle")
            if local.quit:
                self.bus.publish(B.NOTICE, text="Shutting down.", level="info")
                self.request_shutdown()
            return

        # "make it yours": change JARVIS's voice / speaking pace / wake word (local, instant)
        from .voices import parse_command as _voice_cmd
        vc = _voice_cmd(text)
        if vc:
            return self._local_reply(self._apply_voice_change(*vc))

        # multilingual (#63): switch listening + speaking language, live
        from .lang import parse_command as _lang_cmd
        lc = _lang_cmd(text)
        if lc:
            return self._local_reply(self._apply_language_change(*lc))

        # trivia answered locally (time, date, greeting, simple maths, "who are you") — no Claude
        # call, so it never costs usage and works offline
        from .offline_brain import basics_answer
        trivia = basics_answer(text, self.cfg)
        if trivia:
            return self._local_reply(trivia)

        # live notetaker (#57): start / stop a timestamped meeting transcript
        from . import notetaker
        if notetaker.is_start_trigger(text):
            notetaker.start()
            return self._local_reply('I\'m taking notes now. Say "stop taking notes" when you\'re done.')
        if notetaker.is_stop_trigger(text):
            path = notetaker.stop()
            if not path:
                return self._local_reply("I wasn't taking notes just now.")
            self.bus.publish(B.NOTICE, text="Wrapping up your notes.", level="info")
            text, expanded = notetaker.summary_prompt(path, self.cfg), True
        else:
            notetaker.feed(text)        # if a session is active, capture this utterance
            expanded = False

        # push the last reply to the phone (#61): opt-in, user-initiated each time
        from . import push
        if not expanded and push.is_configured(self.cfg) and \
                re.search(r"\b(send|push|text)\b.*\bto my phone\b", text, re.I):
            ok = push.send("JARVIS", self._last_reply or "Nothing to send yet.", self.cfg)
            return self._local_reply("Sent to your phone." if ok else "I couldn't reach your phone just now.")

        # follow-up reference (#70): "read the one from the bank" after a status briefing
        if not expanded:
            from .followup import is_followup, resolve_now
            if is_followup(text):
                res = resolve_now(text)
                if res:
                    self.bus.publish(B.NOTICE, text=f"On it — the one from {res[0]}.", level="info")
                    text, expanded = res[1], True

        # morning brief / status / studies / dev triggers → full brain instructions
        if not expanded:
            from .brief import brief_prompt, is_brief_trigger, is_status_trigger, status_prompt
            if is_brief_trigger(text):
                self.bus.publish(B.NOTICE, text="Putting your brief together.", level="info")
                text = brief_prompt(self.cfg)
            elif is_status_trigger(text):
                self.bus.publish(B.NOTICE, text="Checking your mail.", level="info")
                text = status_prompt(self.cfg)
            else:
                from .diffreview import is_trigger as is_diff_trigger, prompt as diff_prompt
                from .clipboard import build_prompt, get_text, is_trigger
                from .tutor import is_trigger as tutor_t, topic_of as tutor_topic, tutor_prompt
                from .revision import is_trigger as rev_t, topic_of as rev_topic, revision_prompt
                from .recap import is_trigger as recap_t, span_of, recap_prompt
                from .scheduling import is_trigger as sch_t, scheduling_prompt
                from .prflow import is_trigger as pr_t, pr_prompt
                from . import components as _comp, waketrain as _wt
                if is_diff_trigger(text):
                    self.bus.publish(B.NOTICE, text="Reading your changes.", level="info")
                    text = diff_prompt(str(self.sessions.cwd))
                elif is_trigger(text):
                    # clipboard genie: "explain / translate / tidy what I just copied"
                    clip = get_text()
                    if not clip:
                        return self._local_reply("There's nothing I can read on your clipboard.")
                    self.bus.publish(B.NOTICE, text="Working on what you copied.", level="info")
                    text = build_prompt(text, clip)
                elif tutor_t(text):
                    self.bus.publish(B.NOTICE, text="Let's study.", level="info")
                    text = tutor_prompt(tutor_topic(text), self.cfg)
                elif rev_t(text):
                    self.bus.publish(B.NOTICE, text="Revision time.", level="info")
                    text = revision_prompt(self.cfg, rev_topic(text))
                elif recap_t(text):
                    self.bus.publish(B.NOTICE, text="Pulling your recap together.", level="info")
                    text = recap_prompt(self.cfg, span_of(text))
                elif sch_t(text):
                    self.bus.publish(B.NOTICE, text="Finding you a slot.", level="info")
                    text = scheduling_prompt(text, self.cfg)
                elif pr_t(text):
                    self.bus.publish(B.NOTICE, text="Preparing the pull request.", level="info")
                    text = pr_prompt(str(self.sessions.cwd), self.cfg)
                elif _comp.use_name(text):
                    name = _comp.use_name(text)
                    code = _comp.get(name)
                    if not code:
                        return self._local_reply(f"I don't have a saved component called {name}.")
                    self.bus.publish(B.NOTICE, text=f"Building with your {name}.", level="info")
                    text = _comp.use_prompt(name, code, text)
                elif _wt.is_trigger(text):
                    text = ("The user wants to train a custom wake word. Ask them for the exact phrase "
                            "if it isn't clear, then run `python -m jarvis.waketrain plan \"<phrase>\"` "
                            "and `python -m jarvis.waketrain synth \"<phrase>\"` via Bash and walk them "
                            "through the steps those print. Don't say it's trained until an .onnx exists.")

        # #18: a saved routine's trigger phrase expands into its instruction for the brain
        if not expanded:
            routine = self.routines.match(text)
            if routine:
                name, prompt = routine
                self.bus.publish(B.NOTICE, text=f"Running the {name} routine.", level="info")
                text = prompt

        self.bus.set_state("thinking")
        stripper, splitter = TagStripper(), SentenceSplitter()
        full = []

        def on_text(chunk: str) -> None:
            visible = stripper.feed(chunk)
            if not visible:
                return
            full.append(visible)
            if self.bus.state != "speaking" and self.speaker:
                self.bus.set_state("speaking")
            for s in splitter.feed(visible):
                self.speak(s)
            self.bus.say_line("jarvis", "".join(full).strip(), final=False)

        def on_tool(name: str, inp: dict) -> None:
            short = name.split("__")[-1].replace("_", " ")
            spoken, _ = describe_action(name, inp)
            decision = self.policy.classify(name, inp)
            if decision.action == CONFIRM:
                self.bus.publish(B.TOOL, name=name, summary=spoken, phase="start")
            else:
                self.bus.publish(B.TOOL, name=name, summary=spoken, phase="start")
            if name.startswith("mcp__jarvis-desktop__") and short not in (
                    "list windows", "active window text", "ocr screen", "screenshot"):
                self.bus.set_state("acting")

        res = self.sessions.ask(text, on_text, on_tool)
        tail = stripper.flush()
        if tail:
            full.append(tail)
            for s in splitter.feed(tail) + splitter.flush():
                self.speak(s)
        final_text = "".join(full).strip()
        if res.is_error and not res.cancelled:
            from .offline_brain import answer as offline_answer, is_offline_error
            from .authwatch import advice as auth_advice, is_auth_error, note_auth_failure
            if is_auth_error(res.error) and not final_text:
                # the headless Claude sign-in couldn't authenticate (#51)
                note_auth_failure()
                reply = auth_advice() or ("My Claude sign-in needs attention — please run "
                                          "`claude setup-token` so I can keep working.")
                self.bus.say_line("jarvis", reply, final=True)
                self.speak(reply)
                self.bus.publish(B.NOTICE, text="Sign-in problem — run claude setup-token.", level="error")
            elif is_offline_error(res.error) and not final_text:
                # network is down and the cloud brain never replied — field it locally
                from . import screensense
                if screensense.is_trigger(original):
                    reply = screensense.answer(original, screensense.read_screen_text())  # #69
                else:
                    reply = offline_answer(text, self.cfg)
                self.bus.say_line("jarvis", reply, final=True)
                self.speak(reply)
                self.bus.publish(B.NOTICE, text="Offline — answered locally.", level="warn")
            else:
                self.bus.say_line("jarvis", final_text, final=True)
                self.bus.publish(B.NOTICE, text=f"Error: {res.error}", level="error")
                self.speak("Sorry, something went wrong with that request.")
        else:
            self.bus.say_line("jarvis", final_text, final=True)
        if final_text:
            self._last_reply = final_text
        self._emit_session()

        if self.speaker:
            while self.speaker.speaking and not self.interrupted.is_set():
                time.sleep(0.05)
        self.bus.set_state("idle")

    def _local_reply(self, reply: str) -> None:
        """Speak a locally-produced answer and settle back to idle (no brain call)."""
        self.bus.say_line("jarvis", reply)
        self.speak(reply)
        self._last_reply = reply
        self._emit_session()
        self.bus.set_state("idle")
        return None

    def _apply_language_change(self, lang: str, code: str, voice: str) -> str:
        """Switch the listening language + speaking voice (live and in config)."""
        from .lang import set_language
        ok = set_language(code, voice)
        if self.speaker:
            self.speaker.voice = voice
        if self.listener:
            try:
                self.listener.language = code
            except Exception:
                pass
        self.cfg.setdefault("voice", {})["tts_voice"] = voice
        self.cfg["voice"]["language"] = code
        return (f"Switching to {lang}." if ok else
                f"I'll speak {lang} now, though I couldn't save it to config.")

    def _apply_voice_change(self, kind: str, val: str) -> str:
        """Apply a voice/pace/wake-word change to config and (where possible) the live speaker."""
        from .voices import BUILTIN_WAKE, bump_rate, set_rate, set_voice, set_wake
        if kind == "voice":
            ok = set_voice(val)
            if ok and self.speaker:
                self.speaker.voice = val
            self.cfg.setdefault("voice", {})["tts_voice"] = val
            return "How's this voice, sir?" if ok else "I couldn't change my voice just now."
        if kind == "rate":
            new = bump_rate(val)
            if new and set_rate(new):
                if self.speaker:
                    self.speaker.rate = new
                self.cfg.setdefault("voice", {})["tts_rate"] = new
                return "Is this pace better?"
            return "I couldn't change my speaking pace."
        if kind == "wake":
            if val not in BUILTIN_WAKE and not val.endswith(".onnx"):
                opts = ", ".join(w.replace("_", " ") for w in BUILTIN_WAKE[:4])
                return (f"I can switch to a built-in wake word such as {opts}. A brand-new word like "
                        f"'{val.replace('_', ' ')}' needs a trained model — point wake_model at its .onnx file.")
            ok = set_wake(val)
            return (f"Wake word set to {val.replace('_', ' ')}. Restart me and I'll listen for it."
                    if ok else "I couldn't change the wake word.")
        return "I'm not sure how to change that."

    # ----------------------------------------------------------------- control
    def kill_switch(self) -> None:
        STOP_FILE.touch()
        self.interrupted.set()
        self.brain.cancel()
        if self.speaker:
            self.speaker.stop()
        # unblock any confirmation as a deny
        with self._pending_lock:
            if self._pending and not self._pending.event.is_set():
                self._pending.answer = Answer(False, message="Stopped by the user's kill switch.")
                self._pending.event.set()
        self.bus.publish(B.NOTICE, text="Stopped. Desktop actions are blocked until your next request.", level="warn")
        self.bus.set_state("idle")

    def control(self, action: str) -> None:
        if action == "stop":
            self.kill_switch()
        elif action == "new_session":
            self.sessions.new_session()
            self._emit_session()
            self.bus.publish(B.NOTICE, text="Fresh session started.", level="info")
        elif action in ("mute", "unmute") and self.speaker:
            self.bus.publish(B.NOTICE, text=action + "d", level="info")

    _shutdown = None

    def request_shutdown(self) -> None:
        if self._shutdown:
            self._shutdown.set()
