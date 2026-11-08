"""Decide whether a tool call is allowed, needs confirmation, or is blocked outright.

Loaded from command_policy.yaml plus a few checks built in here (so the most dangerous
ones can't be disabled by editing the file). Used by the permission flow before any
command runs, and by a Claude Code PreToolUse hook as a second line of defence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

POLICY_FILE = Path(__file__).resolve().parent / "command_policy.yaml"

ALLOW, CONFIRM, BLOCK = "allow", "confirm", "block"

# Shell tools whose "command" we inspect; everything else is treated as an opaque tool call.
SHELL_TOOLS = {"Bash", "PowerShell", "mcp__desktop-commander__start_process",
               "mcp__desktop-commander__interact_with_process"}
# These desktop/file tools always need confirmation (they change the screen or disk).
DESKTOP_ACTION_PREFIX = "mcp__jarvis-desktop__"
DESKTOP_READ_TOOLS = {"list_windows", "active_window_text", "ocr_screen", "screenshot"}
WRITE_TOOLS = {"Write", "Edit", "NotebookEdit", "mcp__desktop-commander__write_file",
               "mcp__desktop-commander__edit_block"}

_SYSTEM_ROOTS = [
    r"c:\\windows", r"c:\\program files", r"c:\\program files \(x86\)",
    r"[a-z]:\\?$", r"[a-z]:[\\/]\*", r"/", r"~",
]


@dataclass
class Decision:
    action: str          # allow | confirm | block
    rule: str = ""       # which rule matched
    reason: str = ""     # why (shown/spoken to the user, or sent back to Claude on block)

    @property
    def speakable(self) -> str:
        return self.reason or self.rule


def _norm(cmd: str) -> str:
    """Strip quoting/escaping tricks so a pattern can't be dodged by r"m" or r^m."""
    return re.sub(r"[`'\"^]", "", cmd).lower()


def _expand(path: str) -> str:
    return str(Path(path.strip().strip('"\'')).expanduser()).replace("\\", "/").lower().rstrip("/")


# --------------------------------------------------------------------------- built-in checks

def _is_recursive_delete(c: str) -> bool:
    return bool(
        re.search(r"\brm\b[^|;&\n]*\s-[a-z]*r[a-z]*f|\brm\b[^|;&\n]*\s-[a-z]*f[a-z]*r", c)
        or re.search(r"remove-item\b[^|;&\n]*(-recurse|\s-r\b)[^|;&\n]*(-force|\s-f\b)", c)
        or re.search(r"remove-item\b[^|;&\n]*(-force|\s-f\b)[^|;&\n]*(-recurse|\s-r\b)", c)
        or re.search(r"\b(rd|rmdir)\b[^|;&\n]*/s\b", c)
        or re.search(r"\bdel\b[^|;&\n]*/s\b", c)
        or re.search(r"\bget-childitem\b.*\|\s*remove-item\b.*(-recurse|-force)", c)
    )


def _touches_system_path(c: str) -> bool:
    for root in _SYSTEM_ROOTS:
        if re.search(r"(remove-item|rm|del|rd|rmdir|erase)\b[^|;&\n]*(?<![\w/\\])" + root, c):
            return True
    return False


def _is_any_delete(c: str) -> bool:
    return bool(re.search(r"\b(rm|del|erase|rd|rmdir)\b|\bremove-item\b|\bremove-itemproperty\b", c))


def _is_self_protection(c: str, protected: list[str]) -> bool:
    targets = ["command_policy.yaml", "jarvis/policy.py", "policy.py", "jarvis\\policy.py"]
    if any(t in c for t in targets) and re.search(r"\b(rm|del|remove-item|set-content|out-file|>|>>|mv|move|copy|cp|ni\b|new-item)\b|>\s*", c):
        return True
    return False


_BUILTINS = {
    "recursive_delete": lambda c, p: _is_recursive_delete(c),
    "delete_system_path": lambda c, p: _touches_system_path(c),
    "any_delete": lambda c, p: _is_any_delete(c),
    "self_protection": lambda c, p: _is_self_protection(c, p),
}


class Policy:
    def __init__(self, path: Path | None = None):
        self.path = path or POLICY_FILE
        data: dict[str, Any] = {}
        if self.path.exists():
            data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        self.block_rules = data.get("block") or []
        self.confirm_rules = data.get("confirm") or []
        self.protected_paths = [_expand(p) for p in (data.get("protected_paths") or [])]
        for group in (self.block_rules, self.confirm_rules):
            for rule in group:
                if rule.get("pattern"):
                    rule["_re"] = re.compile(rule["pattern"], re.I | re.S)

    # -- command classification
    def _match(self, rules: list[dict], cmd_norm: str) -> dict | None:
        for rule in rules:
            if rule.get("builtin") and _BUILTINS[rule["builtin"]](cmd_norm, self.protected_paths):
                return rule
            r = rule.get("_re")
            if r and r.search(cmd_norm):
                return rule
        return None

    def classify_command(self, command: str) -> Decision:
        c = _norm(command)
        if rule := self._match(self.block_rules, c):
            return Decision(BLOCK, rule["name"], rule.get("reason", ""))
        if rule := self._match(self.confirm_rules, c):
            return Decision(CONFIRM, rule["name"], rule.get("reason", ""))
        return Decision(ALLOW)

    def _writes_protected(self, path: str) -> Decision | None:
        p = _expand(path)
        for prot in self.protected_paths:
            if p == prot or p.startswith(prot + "/"):
                return Decision(BLOCK, "Protected path",
                                f"{path} is protected and can't be changed by JARVIS.")
        return None

    # -- full tool-call classification
    def classify(self, tool: str, tool_input: dict | None) -> Decision:
        tool_input = tool_input or {}
        if tool in SHELL_TOOLS:
            cmd = str(tool_input.get("command") or tool_input.get("input") or "")
            if not cmd:
                return Decision(ALLOW)
            return self.classify_command(cmd)
        if tool in WRITE_TOOLS:
            path = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("notebook_path")
            if path and (d := self._writes_protected(str(path))):
                return d
            return Decision(ALLOW)
        if tool.startswith(DESKTOP_ACTION_PREFIX):
            short = tool[len(DESKTOP_ACTION_PREFIX):]
            if short in DESKTOP_READ_TOOLS:
                return Decision(ALLOW)
            return Decision(CONFIRM, "Desktop action", "This controls the mouse or keyboard.")
        return Decision(ALLOW)


_POLICY: Policy | None = None


def get_policy(reload: bool = False) -> Policy:
    global _POLICY
    if _POLICY is None or reload:
        _POLICY = Policy()
    return _POLICY
