"""Self-test for the jarvis-desktop MCP server. Run from the DUME root:

    .venv\\Scripts\\python -m jarvis.desktop_mcp.selftest              # full test (takes the focus)
    .venv\\Scripts\\python -m jarvis.desktop_mcp.selftest --read-only  # read tools only, no input

Starts the server over stdio like the Claude Code CLI does, exercises the read tools, then a harmless
action test: opens Notepad, makes a new empty tab, types "hello from jarvis", reads it back, and leaves
Notepad open (nothing is saved, nothing else is closed). Finally checks that logs\\STOP blocks actions.
Don't touch the mouse or keyboard while the action test runs (about 15 seconds).
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[2]
STOP_FILE = ROOT / "logs" / "STOP"
ACTION_LOG = ROOT / "logs" / "actions.log"
PHRASE = "hello from jarvis"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    return ok


def text_of(res) -> str:
    return "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")


def head(s: str, n: int = 8) -> str:
    lines = s.splitlines()
    more = f"\n    ... ({len(lines) - n} more lines)" if len(lines) > n else ""
    return "\n".join("    " + ln[:160] for ln in lines[:n]) + more


async def call(session: ClientSession, tool: str, **args) -> str:
    t0 = time.monotonic()
    res = await session.call_tool(tool, args)
    out = text_of(res)
    print(f"\n> {tool}({', '.join(f'{k}={v!r}' for k, v in args.items())})  [{time.monotonic() - t0:.2f}s]")
    print(head(out))
    return out


async def main(read_only: bool = False) -> int:
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    params = StdioServerParameters(command=sys.executable, args=["-m", "jarvis.desktop_mcp.server"],
                                   cwd=str(ROOT), env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            t0 = time.monotonic()
            await session.initialize()
            tools = sorted(t.name for t in (await session.list_tools()).tools)
            print(f"server up in {time.monotonic() - t0:.2f}s; {len(tools)} tools: {', '.join(tools)}")
            expected = {"list_windows", "active_window_text", "ocr_screen", "screenshot", "focus_window",
                        "open_app", "click", "click_element", "type_text", "hotkey", "scroll"}
            check("all tools listed", expected <= set(tools), ", ".join(sorted(expected - set(tools))))

            # ---------------- read tools ----------------
            out = await call(session, "list_windows")
            check("list_windows", out.split()[1:2] == ["windows"] and "ERROR" not in out)
            out = await call(session, "active_window_text", max_chars=1200)
            check("active_window_text", out.startswith("Window ") and len(out.splitlines()) > 1)
            out = await call(session, "ocr_screen", x=0, y=0, w=800, h=200)
            check("ocr_screen (800x200 region)", out.startswith("OCR of region"))
            t1 = time.monotonic()
            res = await session.call_tool("screenshot", {"scale": 0.5})
            kinds = [c.type for c in res.content]
            img = next((c for c in res.content if c.type == "image"), None)
            size = len(img.data) * 3 // 4 if img else 0
            print(f"\n> screenshot(scale=0.5)  [{time.monotonic() - t1:.2f}s]\n    content: {kinds}, "
                  f"PNG ~{size // 1024} KB\n    {text_of(res)}")
            mime = getattr(img, "mime_type", None) or getattr(img, "mimeType", None)  # mcp 2.x / 1.x
            check("screenshot", mime == "image/png" and "scale 0.5" in text_of(res))

            # ---------------- harmless action test ----------------
            if read_only:
                print("\n--read-only: skipping the Notepad action test and the kill-switch test")
                return summary()
            if STOP_FILE.exists():
                check("action test", False, "logs\\STOP already exists (kill switch on); skipped, file left alone")
                return summary()
            print("\n--- action test: Notepad. It takes the focus; hands off the mouse and keyboard ---")
            for n in (3, 2, 1):
                print(f"    starting in {n}...", flush=True)
                await asyncio.sleep(1)
            out = await call(session, "open_app", name_or_path="notepad")
            check("open_app notepad", "ERROR" not in out and "Notepad" in out)
            out = await call(session, "focus_window", title_contains="Notepad")
            if not check("focus_window Notepad", out.startswith("focused")):
                return summary()
            # A fresh tab, so we never type into one of the user's restored Notepad tabs.
            out = await call(session, "hotkey", keys="ctrl+n", title_contains="Notepad")
            check("hotkey ctrl+n (new tab)", out.startswith("pressed"))
            await asyncio.sleep(0.8)
            out = await call(session, "active_window_text", contains="Document", max_chars=800)
            doc = [ln for ln in out.splitlines()[1:] if ln.strip().startswith("Document")]
            if not check("new tab is empty", bool(doc) and "value=" not in doc[0], doc[0].strip() if doc else out):
                print("    not typing: the focused Notepad document is not empty")
                return summary()
            out = await call(session, "click_element", name="Text editor", control_type="Document",
                             title_contains="Notepad")
            check("click_element 'Text editor'", out.startswith("clicked"))
            out = await call(session, "type_text", text=PHRASE, title_contains="Notepad")
            check("type_text", out.startswith("typed") and "verified" in out)
            await asyncio.sleep(0.4)
            out = await call(session, "active_window_text", contains=PHRASE, max_chars=800)
            check("read back via active_window_text", f"value='{PHRASE}'" in out)

            # ---------------- kill switch ----------------
            print("\n--- kill switch: logs\\STOP ---")
            STOP_FILE.write_text("selftest", encoding="utf-8")
            try:
                out = await call(session, "type_text", text=" SHOULD NOT APPEAR", title_contains="Notepad")
                check("STOP refuses type_text", out.startswith("REFUSED"))
                out = await call(session, "click", x=10, y=10)
                check("STOP refuses click", out.startswith("REFUSED"))
                out = await call(session, "active_window_text", contains="SHOULD NOT APPEAR", max_chars=800)
                hits = [ln for ln in out.splitlines()[1:] if not ln.startswith("(no control line")]
                check("refused text never typed", not hits, hits[0].strip() if hits else "")
            finally:
                STOP_FILE.unlink(missing_ok=True)

            tail = ACTION_LOG.read_text(encoding="utf-8").splitlines()[-3:] if ACTION_LOG.exists() else []
            recs = [json.loads(ln) for ln in tail]
            check("actions.log records actions", any(r["tool"] == "type_text" and r["result"].startswith("REFUSED")
                                                     for r in recs), f"{len(tail)} recent lines")
            print("    last log line: " + (tail[-1][:200] if tail else "(none)"))
    return summary()


def summary() -> int:
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" +
          (f"; FAILED: {', '.join(failed)}" if failed else ""))
    if any(n.startswith("type_text") for n, _ok, _d in results):
        print("Notepad was left open with an unsaved tab containing the test text; close it without saving.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(asyncio.run(main(read_only="--read-only" in sys.argv[1:])))
