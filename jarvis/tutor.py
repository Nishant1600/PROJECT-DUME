"""Study tutor (#55): "teach me <topic>".

A trigger phrase expands into a full instruction for the brain: explain the topic
clearly at the user's level, then save structured notes — locally to the study
knowledge base (so revision mode can quiz from them) and, when Notion is connected,
as a page. Expanding the phrase (rather than a canned reply) means the brain actually
teaches and persists each time.
"""
from __future__ import annotations

import re

from . import studykb  # noqa: F401  (kept for callers that want the store)

_TRIGGER = re.compile(
    r"^(?:/tutor|/study|"
    r"(?:be my tutor (?:on|for)|tutor me (?:on|about|in)|teach me (?:about |on )?|"
    r"give me a lesson (?:on|about)|start a lesson (?:on|about)|lesson on|"
    r"study tutor|help me (?:study|learn|revise)|study)\b)\s*(?P<topic>.*)$", re.I)


def is_trigger(text: str) -> bool:
    m = _TRIGGER.match((text or "").strip())
    return bool(m) and bool(topic_of(text))


def topic_of(text: str) -> str:
    m = _TRIGGER.match((text or "").strip())
    if not m:
        return ""
    topic = (m.group("topic") or "").strip(" .?!")
    topic = re.sub(r"^(?:the\s+(?:topic|concept|subject)\s+(?:of\s+)?|about\s+)", "", topic, flags=re.I)
    return topic.strip()


def tutor_prompt(topic: str, cfg: dict | None = None) -> str:
    """Instruction the brain runs to teach a topic and persist notes."""
    integ = (cfg or {}).get("integrations", {}) if cfg else {}
    notion = " Also save a tidy page to my Notion study space." if integ.get("use_my_connectors") else ""
    return (
        f"Be my study tutor for: {topic}.\n"
        "Teach it the way a good tutor would, out loud and concise: start with the core idea in "
        "plain language, then the key points, a simple example or analogy, and one common pitfall. "
        "Pitch it at an undergraduate level unless I say otherwise, and keep the spoken part to a "
        "few sentences — offer to go deeper rather than dumping everything.\n"
        "Then save structured revision notes so I can study them later: write concise Markdown notes "
        "(a short summary, the key points as bullets, and 3-5 self-test questions with answers) and "
        "pipe them to Bash:\n"
        f'  python -m jarvis.studykb save "{topic}"   (notes on stdin)\n'
        f"Confirm in one line that the notes are saved.{notion}"
    )


def summary(text: str) -> str:
    """A short spoken acknowledgement while the tutor spins up."""
    t = topic_of(text) or "that"
    return f"Let's study {t}."
