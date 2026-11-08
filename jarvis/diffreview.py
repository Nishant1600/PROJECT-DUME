"""Spoken diff review for JARVIS (README roadmap: "walk me through my changes before you commit").

Say "walk me through my changes" and JARVIS reads your working-tree diff and talks you through
it in plain speech — file by file, what changed and why it matters, flagging anything risky —
without committing anything. It's the friendly, out-loud cousin of the deeper code review in
`python -m jarvis.repo diff`.
"""
from __future__ import annotations

import re

_TRIGGER = re.compile(
    r"^(?:/diffreview|"
    r"(?:walk|talk|take|go)\s+me\s+through\s+(?:my|the|these)\s+(?:changes|diff|edits)|"
    r"go\s+through\s+(?:my|the)\s+(?:changes|diff)|"
    r"what\s+did\s+i\s+change|what\s+have\s+i\s+changed|"
    r"explain\s+my\s+(?:changes|diff))\W*$", re.I)


def is_trigger(text: str) -> bool:
    return bool(_TRIGGER.match((text or "").strip()))


def prompt(project_dir: str | None = None) -> str:
    where = f' for the project at "{project_dir}"' if project_dir else " for the project I'm currently in"
    return (
        f"Walk me through my uncommitted changes{where}. Get them by running "
        "`python -m jarvis.repo diff .` via Bash (add `--staged` as well if the working tree is "
        "clean but something is staged). Then talk me through them out loud: go file by file, say "
        "in plain words what changed and why it matters, and call out anything risky or unfinished "
        "first. Keep it conversational and spoken — don't read the raw diff or code aloud. Do NOT "
        "commit, stage or change anything; this is just a walkthrough. End by asking if I want to "
        "commit."
    )
