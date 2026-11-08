"""Claude Code PreToolUse hook: a second line of defence for blocked commands.

Even if JARVIS is run with a tool pre-approved (so the permission prompt is skipped),
this hook runs before every Bash/PowerShell/Write/desktop tool call and denies anything
the policy marks `block`. It reads the tool call from stdin and prints a decision as JSON.

Registered automatically in .claude/settings.json by jarvis/main.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except Exception:
        print(json.dumps({})); return
    tool = event.get("tool_name", "")
    tool_input = event.get("tool_input") or {}
    try:
        from jarvis.policy import BLOCK, get_policy

        decision = get_policy().classify(tool, tool_input)
    except Exception as e:
        # Never wedge Claude Code because the guard itself errored.
        print(json.dumps({"systemMessage": f"JARVIS guard error (allowing): {e}"})); return

    if decision.action == BLOCK:
        reason = (f"BLOCKED by JARVIS safety policy — {decision.rule}: {decision.reason} "
                  "Do not retry this or any equivalent command.")
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }))
        return
    print(json.dumps({}))


if __name__ == "__main__":
    main()
