"""Who you are, and how you work — the part of JARVIS that learns and remembers you.

Three living files under memory/:
  identity.md  - stable facts: your name, how to address you, role, machine (rarely changes).
  profile.md   - learned working patterns: hours, projects, preferred tools/languages,
                 how you phrase things, recurring tasks. Updated slowly in the background.
  facts.md     - individual things you asked JARVIS to remember.

All three are injected into every new Claude session so JARVIS starts already knowing you.
The profile is distilled by a cheap model from each conversation, so it sharpens over time
without you doing anything.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .config import MEMORY_DIR

IDENTITY_FILE = MEMORY_DIR / "identity.md"
PROFILE_FILE = MEMORY_DIR / "profile.md"
RULES_FILE = MEMORY_DIR / "rules.md"
DESIGN_FILE = MEMORY_DIR / "design.md"
TOPICS_FILE = MEMORY_DIR / "topics.md"

# Context-budget guard (#24): hard caps so "always-loaded" memory never eats the plan.
MAX_RULES_CHARS = 1400      # rulebook rides in the system prompt every turn — keep it tight
MAX_DESIGN_CHARS = 1600     # learned website-design patterns
MAX_CONTEXT_CHARS = 7000    # whole "what I know about you" block, injected once per session

RULES_SEED = """<!--
  JARVIS Rulebook - your standing rules, like Claude's own rule section.
  JARVIS reads this every session and treats it as authoritative.
  Edit it freely. KEEP IT SHORT: it is loaded on every run, so long text costs tokens
  (anything past ~1400 characters is dropped). This top comment block is ignored.
-->

# Hard rules (never break)
- Never run `rm -rf` or any destructive/recursive delete. Ask before anything irreversible.
- Never send an email, submit a form/application, post publicly, buy, or delete without my explicit "yes".

# Tone
- Be concise and direct. No filler. Address me as Sir.

# Website design defaults
- (JARVIS fills these in as it learns how I build sites; or set your own: stack, fonts, colours, layout.)

# Preferences
- Keep Claude usage low: prefer the cheapest approach that does the job.
"""

TOPICS_SEED = """<!--
  Topics you follow, for "tell me what's new" / "anything new in X?" (feature #42).
  JARVIS reads this and uses web search to surface recent changes.
  Add or remove lines freely, or just tell JARVIS "follow <topic>" / "stop following <topic>".
-->

# Topics I follow
- Three.js and WebGL
- React and Next.js
- AI and Claude / LLM tooling
- Web design and front-end trends
"""


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write(p: Path, text: str) -> None:
    p.write_text(text.strip() + "\n", encoding="utf-8")


class Profile:
    """Reads/writes the identity + profile, and asks a cheap model to keep the profile current."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.address_as = cfg.get("address_as") or "sir"
        self.ensure_identity()
        self.ensure_rules()
        if not TOPICS_FILE.exists():
            _write(TOPICS_FILE, TOPICS_SEED)
        from .mailstyle import ensure as _ensure_mailstyle
        _ensure_mailstyle()

    # ------------------------------------------------------------- identity
    def ensure_identity(self) -> None:
        """Seed identity.md on first run from config + the OS, so JARVIS always knows your name."""
        if _read(IDENTITY_FILE):
            return
        import getpass

        name = self.cfg.get("name") or ""
        if not name:
            # fall back to the Windows account's display/user name
            try:
                name = getpass.getuser()
            except Exception:
                name = ""
        lines = [
            "# Identity",
            f"- Name: {name or 'unknown'}",
            f"- Address me as: {self.address_as}",
            "- Operating system: Windows 11",
        ]
        _write(IDENTITY_FILE, "\n".join(lines))

    @property
    def name(self) -> str:
        m = re.search(r"-\s*Name:\s*(.+)", _read(IDENTITY_FILE))
        return m.group(1).strip() if m else ""

    def set_identity(self, name: str | None = None, address_as: str | None = None) -> None:
        text = _read(IDENTITY_FILE) or "# Identity"
        if name:
            text = re.sub(r"-\s*Name:.*", f"- Name: {name}", text) if "Name:" in text \
                else text + f"\n- Name: {name}"
        if address_as:
            self.address_as = address_as
            text = re.sub(r"-\s*Address me as:.*", f"- Address me as: {address_as}", text) \
                if "Address me as:" in text else text + f"\n- Address me as: {address_as}"
        _write(IDENTITY_FILE, text)

    # ------------------------------------------------------------- rulebook (#21)
    def ensure_rules(self) -> None:
        """Seed memory/rules.md on first run with a short, editable template."""
        if not RULES_FILE.exists():
            _write(RULES_FILE, RULES_SEED)

    @staticmethod
    def _strip_and_cap(text: str, max_chars: int) -> str:
        text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
        text = re.sub(r"(?m)^\s*(?:<!--|-->).*$", "", text).strip()  # drop any stray comment lines
        if len(text) > max_chars:
            text = text[:max_chars].rsplit("\n", 1)[0].rstrip() + "\n… (truncated; keep it short)"
        return text

    def rules_text(self) -> str:
        """The active rulebook, comments stripped and capped (#24 budget guard)."""
        return self._strip_and_cap(_read(RULES_FILE), MAX_RULES_CHARS)

    def design_text(self) -> str:
        """Learned website-design patterns (#23), capped. Empty until JARVIS learns some."""
        return self._strip_and_cap(_read(DESIGN_FILE), MAX_DESIGN_CHARS)

    # ------------------------------------------------------------- corrections (#22)
    # Only strong, durable cues become rules — never one-off instructions like "make it blue".
    _CUE_RE = re.compile(
        r"^(?:jarvis[,\s]+|ok(?:ay)?[,\s]+|please[,\s]+)*"
        r"(from now on|always|never|by default|default to|rule)\b[,:]?\s+(.+)", re.I)
    _PREFER_RE = re.compile(r"\bi (?:prefer|always use|usually use|like to use)\b\s+(.+)", re.I)
    _SKIP = {"mind", "mind it", "fear", "say never"}

    def capture_preference(self, user_text: str) -> str | None:
        """If the user stated a durable preference, save it as a rule and return it (else None).

        Pure regex + a file write — no model call — so it's free and takes effect next turn
        (the rulebook rides in the system prompt, re-read every turn)."""
        t = " ".join((user_text or "").split())
        if len(t) < 6 or t.endswith("?"):
            return None
        rule = None
        m = self._CUE_RE.match(t)
        if m:
            cue, clause = m.group(1).lower(), m.group(2).strip()
            if clause.lower().rstrip(".!") in self._SKIP:
                return None
            if cue in ("always", "never") and len(clause.split()) < 2:
                return None   # "always late", "never mind" — not a rule
            rule = {"always": f"Always {clause}", "never": f"Never {clause}",
                    "by default": f"By default, {clause}", "default to": f"By default, {clause}",
                    "from now on": clause[:1].upper() + clause[1:], "rule": clause}[cue]
        if not rule:
            m = self._PREFER_RE.search(t)
            if m:
                rule = "Prefers " + m.group(1).strip()
        if not rule:
            return None
        rule = re.split(r"(?<=[.!?])\s", rule)[0].strip().rstrip(".")   # one sentence
        rule = " ".join(rule.split())[:140]
        return self._append_rule(rule) if len(rule) >= 4 else None

    def _append_rule(self, rule: str) -> str | None:
        """Add a bullet under '# Preferences' in rules.md (dedup, bounded). Returns the rule if added."""
        text = _read(RULES_FILE) or RULES_SEED.strip()
        active = re.sub(r"<!--.*?-->", "", text, flags=re.S)
        if rule.lower() in active.lower():          # already have it (or a superset)
            return None
        if len(active) > MAX_RULES_CHARS + 600:     # don't let the file grow without bound
            return None
        if re.search(r"(?mi)^#+\s*Preferences\b", text):
            text = text.rstrip() + f"\n- {rule}\n"   # Preferences is the last section in the seed
        else:
            text = text.rstrip() + f"\n\n# Preferences\n- {rule}\n"
        _write(RULES_FILE, text)
        return rule

    # ------------------------------------------------------------- context
    def context_block(self, extra: str = "") -> str:
        """The 'who you are' block prepended to the first message of every new session."""
        parts = []
        ident = _read(IDENTITY_FILE)
        if ident:
            parts.append(ident)
        prof = _read(PROFILE_FILE)
        if prof:
            parts.append("# How I work (learned)\n" + prof)
        design = self.design_text()
        if design:
            parts.append("# How I build websites (learned)\n" + design)
        from .accounts import context_summary
        accts = context_summary(self.cfg)
        if accts:
            parts.append(accts)
        from .mailstyle import style_text as _mail_style
        voice = _mail_style()
        if voice:
            parts.append("# How I write email (match this when drafting)\n" + voice)
        if extra:
            parts.append(extra)
        if not parts:
            return ""
        body = "\n\n".join(parts)
        if len(body) > MAX_CONTEXT_CHARS:      # #24: keep the once-per-session block bounded
            body = body[:MAX_CONTEXT_CHARS].rsplit("\n", 1)[0].rstrip() + "\n… (memory trimmed)"
        return ("Here is what you already know about me. Use it naturally; do not recite it.\n\n"
                + body + "\n\n")

    def persona_addendum(self) -> str:
        n = self.name
        who = f"The user's name is {n}. " if n and n.lower() != "unknown" else ""
        base = (f"\n{who}Address the user as '{self.address_as}'. You are their long-running personal "
                "assistant: you remember them between sessions and adapt to how they work.\n")
        rules = self.rules_text()
        if rules:
            base += ("\n# The user's standing rules (authoritative — always follow these)\n"
                     + rules + "\n")
        if self.design_text():
            base += ("\nWhen building or designing a website, follow the user's learned design "
                     "patterns (in the context block) unless they ask otherwise.\n")
        return base

    # ------------------------------------------------------------- learning
    def learn(self, summary: str, brain, model: str | None = None) -> bool:
        """Distil durable working patterns from a session summary into profile.md (merge, don't append)."""
        if not summary.strip():
            return False
        current = _read(PROFILE_FILE)
        prompt = (
            "You maintain a profile of how a user works, for their assistant JARVIS. From the previous "
            "profile and a new conversation summary, write an updated profile of at most 220 words, as "
            "short bullet points under these headings (drop a heading if you have nothing for it):\n"
            "Projects & focus, Tools & languages, Working style & hours, Communication style, "
            "Recurring tasks, Preferences.\n"
            "Keep only durable, repeatable patterns — not one-off events. Merge, deduplicate, and drop "
            "anything stale. Output only the profile.\n\n"
            f"<previous_profile>\n{current or '(empty)'}\n</previous_profile>\n\n"
            f"<new_conversation_summary>\n{summary}\n</new_conversation_summary>"
        )
        from .config import RUNTIME_DIR

        res = brain.oneshot(prompt, cwd=RUNTIME_DIR, model=model)
        if res.is_error or not res.text.strip():
            return False
        stamp = datetime.now().strftime("%Y-%m-%d")
        _write(PROFILE_FILE, f"<!-- updated {stamp} -->\n{res.text.strip()}")
        return True

    # ------------------------------------------------------------- design learning (#23)
    _DESIGN_CUES = ("website", "web site", "css", "tailwind", "react", "next.js", "nextjs",
                    "html", "landing", "frontend", "front-end", " ui ", " ux ", "font",
                    "color", "colour", "layout", "component", "figma", "design", "styling")

    def learn_design(self, summary: str, brain, model: str | None = None) -> bool:
        """When a conversation touched website work, distil durable design patterns into design.md.

        Keyword-gated so non-web sessions never trigger an extra model call (keeps usage low)."""
        s = f" {summary.lower()} "
        if not any(cue in s for cue in self._DESIGN_CUES):
            return False
        current = _read(DESIGN_FILE)
        prompt = (
            "You maintain a note on HOW a specific developer builds websites, for their assistant JARVIS. "
            "From the previous note and a new conversation summary, write an updated note of at most 200 "
            "words as short bullets under these headings (drop any you have nothing for): "
            "Stack & frameworks, Styling & CSS, Fonts, Colours, Layout & spacing, Components & libraries, "
            "Conventions. Keep only durable, repeatable choices the developer actually makes — not one-off "
            "task details. Merge, deduplicate, drop stale. Output only the note.\n\n"
            f"<previous_note>\n{current or '(empty)'}\n</previous_note>\n\n"
            f"<conversation_summary>\n{summary}\n</conversation_summary>"
        )
        from .config import RUNTIME_DIR

        res = brain.oneshot(prompt, cwd=RUNTIME_DIR, model=model)
        if res.is_error or not res.text.strip():
            return False
        stamp = datetime.now().strftime("%Y-%m-%d")
        _write(DESIGN_FILE, f"<!-- updated {stamp} -->\n{res.text.strip()}")
        return True
