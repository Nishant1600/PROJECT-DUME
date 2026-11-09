"""Full PR flow (#67): branch -> commit -> open a PR -> summarise CI, by voice.

Extends the read-only repo helper into one spoken flow. A trigger ("open a PR for this")
expands into an instruction that walks the brain through: check status, branch off the
default branch if needed, commit with a sensible message, push, open the PR with `gh`
and a drafted title/body, then report CI — pausing for my explicit yes before the
outward-facing steps (push and creating the PR), and never committing secrets.

The git/gh actions run through the brain's normal tools and JARVIS's confirmation layer;
this module only orchestrates and supplies a branch-name suggestion.
"""
from __future__ import annotations

import re

_TRIGGER = re.compile(
    r"^(?:/pr|open (?:a |the )?(?:pull request|pr)|create (?:a |the )?(?:pull request|pr)|"
    r"make (?:a |the )?(?:pull request|pr)|raise (?:a |the )?(?:pull request|pr)|"
    r"submit (?:a |the )?(?:pull request|pr)|ship (?:this|it) as a (?:pull request|pr)|"
    r"pr this|put (?:this|it) up as a (?:pull request|pr))\b.*$", re.I)


def is_trigger(text: str) -> bool:
    return bool(_TRIGGER.match((text or "").strip()))


def suggest_branch_name(hint: str, prefix: str = "jarvis") -> str:
    """A tidy branch name from a short hint, e.g. 'jarvis/fix-login-redirect'."""
    words = re.findall(r"[a-z0-9]+", (hint or "").lower())
    stop = {"the", "a", "an", "to", "for", "of", "and", "pr", "pull", "request",
            "this", "that", "please", "jarvis", "open", "create", "make"}
    kept = [w for w in words if w not in stop][:5]
    slug = "-".join(kept) or "update"
    return f"{prefix}/{slug}"[:60]


def pr_prompt(project_dir: str, cfg: dict | None = None) -> str:
    return (
        "Open a pull request for my current work. Do this carefully, in order:\n"
        f"1. Run `python -m jarvis.repo status {project_dir}` via Bash to see the branch and changes. "
        "If it's not a git repo or there are no changes, tell me and stop.\n"
        "2. If I'm on the default branch (main/master), create a new branch with a short, descriptive "
        "name (kebab-case) before committing.\n"
        "3. Stage the changes and show me the commit message you propose. Commit after I approve. Do "
        "NOT commit anything that looks like a secret, token or credential.\n"
        "4. Push the branch — ask me first, pushing is outward-facing.\n"
        "5. Create the PR with `gh pr create`, drafting a clear title and a body that summarises what "
        "changed and why. Show me the title and body and get my explicit yes before creating it.\n"
        f"6. Then run `python -m jarvis.repo pr-status {project_dir}` and tell me the PR's link and CI "
        "state in a sentence.\n"
        "Follow my standing rules throughout, and confirm before each outward-facing step."
    )
