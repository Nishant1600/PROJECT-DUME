"""Permission-prompt MCP server (`--permission-prompt-tool mcp__jarvis-confirm__approve`).

Claude Code calls `approve` before running any tool that isn't pre-approved. We forward
the question to the running JARVIS app over localhost, which asks the user (spoken or typed).
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from mcp.server.mcpserver import MCPServer

server = MCPServer("jarvis-confirm")
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never via a proxy


def _deny(message: str) -> str:
    return json.dumps({"behavior": "deny", "message": message})


@server.tool(structured_output=False)  # the CLI wants plain JSON text, not {"result": ...}
def approve(tool_name: str, input: dict[str, Any], tool_use_id: str | None = None) -> str:
    """Ask the user whether a tool call may run. Returns a Claude Code permission decision."""
    port = os.environ.get("JARVIS_CONFIRM_PORT")
    if not port:
        return _deny("The JARVIS confirmation channel is not running, so this action is blocked.")
    body = json.dumps({"tool_name": tool_name, "input": input}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/confirm", data=body,
        headers={"Content-Type": "application/json",
                 "X-Jarvis-Token": os.environ.get("JARVIS_CONFIRM_TOKEN", "")})
    try:
        with _opener.open(req, timeout=300) as resp:
            answer = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # app closed, timeout...
        return _deny(f"Could not get the user's confirmation ({e}); action blocked.")
    if answer.get("allow"):
        return json.dumps({"behavior": "allow", "updatedInput": input})
    return _deny(answer.get("message") or "The user declined this action. Ask before trying something else.")


if __name__ == "__main__":
    server.run()
