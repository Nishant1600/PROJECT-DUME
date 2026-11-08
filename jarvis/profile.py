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
        if extra:
            parts.append(extra)
        if not parts:
            return ""
        return ("Here is what you already know about me. Use it naturally; do not recite it.\n\n"
                + "\n\n".join(parts) + "\n\n")

    def persona_addendum(self) -> str:
        n = self.name
        who = f"The user's name is {n}. " if n and n.lower() != "unknown" else ""
        return (f"\n{who}Address the user as '{self.address_as}'. You are their long-running personal "
                "assistant: you remember them between sessions and adapt to how they work.\n")

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
