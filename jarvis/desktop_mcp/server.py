"""jarvis-desktop: an MCP server (stdio) that lets the Claude Code CLI see and control the Windows desktop.

Run from the DUME root:

    .venv\\Scripts\\python -m jarvis.desktop_mcp.server

Reading is ordered by token cost: active_window_text (UI Automation text) < ocr_screen < screenshot.
All coordinates are real (physical) screen pixels: the process is per-monitor DPI aware.

Safety: pyautogui's fail-safe is on (slam the mouse into a screen corner to abort), every action tool
refuses while logs\\STOP exists (the orchestrator's kill switch), and every action is appended as one
JSON line to logs\\actions.log. Confirmation of risky actions is the orchestrator's job, not this server's.
"""
import ctypes
import sys
from ctypes import wintypes


def _set_dpi_awareness() -> str:
    """Make every coordinate a real screen pixel. Must run before anything reads screen metrics."""
    try:  # Windows 10 1703+: per-monitor v2
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return "system"
    except (AttributeError, OSError):
        return "unknown"


DPI_MODE = _set_dpi_awareness()

import asyncio  # noqa: E402  (imports below must follow the DPI call)
import functools  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Optional  # noqa: E402

try:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server, Image
    _MCP_V2 = False
except ImportError:  # mcp 2.x renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as _Server, Image
    _MCP_V2 = True

ROOT = Path(__file__).resolve().parents[2]  # the DUME folder, whatever the CLI's cwd is
LOGS = ROOT / "logs"
STOP_FILE = LOGS / "STOP"
ACTION_LOG = LOGS / "actions.log"
LAST_SHOT_FILE = LOGS / "last_screenshot.json"

STOP_MSG = ("REFUSED: the JARVIS kill switch is on (logs\\STOP exists), so no desktop actions run. "
            "Tell the user; they delete logs\\STOP to re-enable actions.")

# ---------------------------------------------------------------------------------------------
# Win32 (ctypes)
# ---------------------------------------------------------------------------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

_HWND = wintypes.HWND
_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, _HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [_EnumProc, wintypes.LPARAM]
user32.GetWindowTextLengthW.argtypes = [_HWND]
user32.GetWindowTextW.argtypes = [_HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [_HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [_HWND]
user32.IsIconic.argtypes = [_HWND]
user32.IsWindow.argtypes = [_HWND]
user32.GetWindowLongPtrW.argtypes = [_HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowThreadProcessId.argtypes = [_HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowRect.argtypes = [_HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetForegroundWindow.restype = _HWND
user32.SetForegroundWindow.argtypes = [_HWND]
user32.BringWindowToTop.argtypes = [_HWND]
user32.ShowWindow.argtypes = [_HWND, ctypes.c_int]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = _HWND
user32.GetAncestor.argtypes = [_HWND, wintypes.UINT]
user32.GetAncestor.restype = _HWND
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
dwmapi.DwmGetWindowAttribute.argtypes = [_HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
SW_RESTORE = 9
VK_MENU = 0x12
VK_MASK = 0xE8  # unassigned virtual key: no app reacts to it
VK_RETURN, VK_TAB = 0x0D, 0x09
KEYEVENTF_EXTENDEDKEY = 0x1
KEYEVENTF_KEYUP = 0x2
KEYEVENTF_UNICODE = 0x4
INPUT_KEYBOARD = 1
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
WHEEL_DELTA = 120
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
CREATE_NO_WINDOW = 0x08000000

class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT


def _key_events(ch: str) -> list:
    """Down+up events for one character: Enter/Tab as real keys, everything else as Unicode input
    (independent of the keyboard layout)."""
    def ev(vk, scan, flags):
        return _INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=_KEYBDINPUT(vk, scan, flags, 0, 0)))
    if ch in ("\n", "\t"):
        vk = VK_RETURN if ch == "\n" else VK_TAB
        return [ev(vk, 0, 0), ev(vk, 0, KEYEVENTF_KEYUP)]
    units = ch.encode("utf-16-le")
    evs = []
    for i in range(0, len(units), 2):
        code = int.from_bytes(units[i:i + 2], "little")
        evs.append(ev(0, code, KEYEVENTF_UNICODE))
        evs.append(ev(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
    return evs


def _send_char(ch: str) -> None:
    evs = _key_events(ch)
    user32.SendInput(len(evs), (_INPUT * len(evs))(*evs), ctypes.sizeof(_INPUT))


_proc_names: dict[int, str] = {}


def _title(h) -> str:
    n = user32.GetWindowTextLengthW(h)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(h, buf, n + 1)
    return buf.value


def _pid(h) -> int:
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    return pid.value


def _proc_name(pid: int) -> str:
    if pid in _proc_names:
        return _proc_names[pid]
    name = "?"
    hp = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if hp:
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(size)):
                name = os.path.basename(buf.value)
        finally:
            kernel32.CloseHandle(hp)
    _proc_names[pid] = name
    return name


def _rect(h) -> tuple[int, int, int, int]:
    """Visible window bounds (without the invisible resize border), in physical pixels."""
    r = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(h, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        user32.GetWindowRect(h, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _cloaked(h) -> bool:
    v = ctypes.c_int(0)
    return dwmapi.DwmGetWindowAttribute(h, DWMWA_CLOAKED, ctypes.byref(v), ctypes.sizeof(v)) == 0 and v.value != 0


def _windows() -> list[dict]:
    """Visible, titled, uncloaked top-level windows in z-order (topmost first)."""
    hwnds: list[int] = []

    @_EnumProc
    def _cb(h, _lparam):
        hwnds.append(h)
        return True

    user32.EnumWindows(_cb, 0)
    fg = user32.GetForegroundWindow()
    out = []
    for h in hwnds:
        if not h or not user32.IsWindowVisible(h):
            continue
        title = _title(h)
        if not title.strip():
            continue
        ex = user32.GetWindowLongPtrW(h, GWL_EXSTYLE)
        if ex & WS_EX_TOOLWINDOW and not ex & WS_EX_APPWINDOW:
            continue
        if _cloaked(h):
            continue
        minimized = bool(user32.IsIconic(h))
        rect = _rect(h)
        if not minimized and (rect[2] - rect[0] <= 1 or rect[3] - rect[1] <= 1):
            continue
        out.append({"hwnd": h, "title": title, "proc": _proc_name(_pid(h)), "rect": rect,
                    "minimized": minimized, "fg": h == fg})
    return out


def _find_window(query: str) -> tuple[Optional[dict], list[dict]]:
    """Best window for a title substring (or 0x-hex handle, or process name). Returns (best, all matches)."""
    wins = _windows()
    q = query.strip().lower()
    if re.fullmatch(r"0x[0-9a-f]+", q):
        hw = int(q, 16)
        hit = [w for w in wins if w["hwnd"] == hw]
        return (hit[0] if hit else None), hit
    hits = [w for w in wins if q in w["title"].lower()] or [w for w in wins if q in w["proc"].lower()]
    hits.sort(key=lambda w: (w["title"].lower() != q, w["minimized"]))  # stable: keeps z-order otherwise
    return (hits[0] if hits else None), hits


def _wdesc(w: dict) -> str:
    return f"0x{w['hwnd']:X} '{w['title']}' ({w['proc']})"


def _fg_desc() -> str:
    h = user32.GetForegroundWindow()
    if not h:
        return "none"
    return f"'{_title(h)}' ({_proc_name(_pid(h))})"


def _wait_fg(h, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while True:
        if user32.GetForegroundWindow() == h:
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(0.05)


def _activate(h) -> bool:
    """Bring a window to the foreground despite Windows' foreground lock."""
    if user32.IsIconic(h):
        user32.ShowWindow(h, SW_RESTORE)
        time.sleep(0.2)
    if user32.GetForegroundWindow() == h:
        return True
    user32.SetForegroundWindow(h)
    if _wait_fg(h, 0.3):
        return True
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    me = kernel32.GetCurrentThreadId()
    attached = bool(fg_tid and fg_tid != me and user32.AttachThreadInput(me, fg_tid, True))
    try:
        user32.BringWindowToTop(h)
        user32.SetForegroundWindow(h)
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_tid, False)
    if _wait_fg(h, 0.3):
        return True
    # Last resort: holding Alt lifts the foreground lock. A lone Alt tap would open the menu of whatever
    # window gets the key-up (e.g. the browser's "..." menu), so mask it with an unassigned key (vkE8),
    # the way AutoHotkey does.
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY, 0)
    try:
        user32.SetForegroundWindow(h)
        user32.keybd_event(VK_MASK, 0, 0, 0)
        user32.keybd_event(VK_MASK, 0, KEYEVENTF_KEYUP, 0)
    finally:
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
    return _wait_fg(h, 0.5)


def _primary_size() -> tuple[int, int]:
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


# ---------------------------------------------------------------------------------------------
# Lazy heavy imports (the CLI starts a fresh server per run, so startup must stay quick)
# ---------------------------------------------------------------------------------------------
_pg_mod = None


def _pg():
    global _pg_mod
    if _pg_mod is None:
        import pyautogui
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.05
        _pg_mod = pyautogui
    return _pg_mod


# ---------------------------------------------------------------------------------------------
# UI Automation
# ---------------------------------------------------------------------------------------------
P_BOUNDS, P_CTYPE, P_FOCUS, P_ENABLED, P_NAME, P_AID, P_PASSWORD, P_OFFSCREEN, P_VALUE = (
    30001, 30003, 30008, 30010, 30005, 30011, 30019, 30022, 30045)
PAT_INVOKE, PAT_SELECTIONITEM, PAT_TEXT, PAT_TOGGLE = 10000, 10010, 10014, 10015
TREESCOPE_CHILDREN = 2

# Controls worth a line even without a name (you can still click or type into them).
_INTERACTIVE = {"Button", "CheckBox", "ComboBox", "Edit", "Hyperlink", "ListItem", "MenuItem",
                "RadioButton", "Slider", "Spinner", "SplitButton", "TabItem", "TreeItem", "Document",
                "DataItem"}
_TEXTY = {"Document", "Edit"}

_uia_state: dict = {}


def _uia() -> dict:
    if not _uia_state:
        from uiautomation import uiautomation as auto  # the inner module also exposes _AutomationClient
        try:
            auto.Logger.SetLogFile(str(LOGS / "uiautomation.log"))
        except Exception:
            pass
        client = auto._AutomationClient.instance()
        iuia = client.IUIAutomation
        cr = iuia.CreateCacheRequest()
        for pid in (P_BOUNDS, P_CTYPE, P_FOCUS, P_ENABLED, P_NAME, P_AID, P_PASSWORD, P_OFFSCREEN, P_VALUE):
            cr.AddProperty(pid)
        names = {k: v[:-7] if v.endswith("Control") else v for k, v in auto.ControlTypeNames.items()}
        _uia_state.update(auto=auto, core=client.UIAutomationCore, iuia=iuia, cr=cr,
                          cond=iuia.ControlViewCondition, names=names)
    return _uia_state


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _node(el) -> dict:
    st = _uia_state
    ct = _safe(lambda: el.CachedControlType, 0)
    r = _safe(lambda: el.CachedBoundingRectangle)
    rect = (r.left, r.top, r.right, r.bottom) if r is not None else (0, 0, 0, 0)
    val = _safe(lambda: el.GetCachedPropertyValue(P_VALUE))
    return {
        "type": st["names"].get(ct, str(ct)),
        "name": _safe(lambda: el.CachedName, "") or "",
        "aid": _safe(lambda: el.CachedAutomationId, "") or "",
        "rect": rect,
        "offscreen": bool(_safe(lambda: el.GetCachedPropertyValue(P_OFFSCREEN), False)),
        "enabled": bool(_safe(lambda: el.GetCachedPropertyValue(P_ENABLED), True)),
        "focused": bool(_safe(lambda: el.GetCachedPropertyValue(P_FOCUS), False)),
        "password": bool(_safe(lambda: el.GetCachedPropertyValue(P_PASSWORD), False)),
        "value": val if isinstance(val, str) else "",
    }


def _children(el) -> list:
    st = _uia_state
    arr = _safe(lambda: el.FindAllBuildCache(TREESCOPE_CHILDREN, st["cond"], st["cr"]))
    if not arr:
        return []
    return [arr.GetElement(i) for i in range(_safe(lambda: arr.Length, 0))]


def _pattern(el, pattern_id: int, iface):
    unk = _safe(lambda: el.GetCurrentPattern(pattern_id))
    if not unk:
        return None
    return _safe(lambda: unk.QueryInterface(iface))


def _doc_text(el, limit: int) -> str:
    tp = _pattern(el, PAT_TEXT, _uia_state["core"].IUIAutomationTextPattern)
    if tp is None:
        return ""
    return _safe(lambda: tp.DocumentRange.GetText(limit + 1), "") or ""


def _clip(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n] + "..."


def _fmt(n: dict, value_chars: int) -> str:
    parts = [n["type"]]
    if n["name"]:
        parts.append("'" + _clip(n["name"], 120) + "'")
    if n["aid"]:
        parts.append(f"[{_clip(n['aid'], 60)}]")
    if n["value"] and n["value"] != n["name"]:
        parts.append("value='" + _clip(n["value"], value_chars) + "'")
    l, t, r, b = n["rect"]
    if r > l and b > t:
        parts.append(f"rect={l},{t},{r},{b}")
    if not n["enabled"]:
        parts.append("disabled")
    if n["focused"]:
        parts.append("focused")
    return " ".join(parts)


def _walk(root, max_depth: int, seconds: float, include_offscreen: bool = False):
    """Depth-first walk of the control view. Yields (element, info, depth, shown_ancestors).

    Children are fetched one cached batch per parent, which is far cheaper than per-property COM calls.
    """
    deadline = time.monotonic() + seconds
    stack = [(root, 0, 0)]
    while stack:
        if time.monotonic() > deadline:
            yield None, {"timeout": True}, 0, 0
            return
        el, depth, shown = stack.pop()
        info = _node(el)
        if depth > 0 and info["offscreen"] and not include_offscreen:
            continue
        info["show"] = depth > 0 and bool(info["name"] or info["value"] or info["type"] in _INTERACTIVE)
        yield el, info, depth, shown
        if depth < max_depth:
            nxt = shown + (1 if info["show"] else 0)
            for kid in reversed(_children(el)):
                stack.append((kid, depth + 1, nxt))


def _root_for(title_contains: Optional[str]):
    st = _uia()
    if title_contains:
        w, _ = _find_window(title_contains)
        if not w:
            raise LookupError(f"no window title or process contains {title_contains!r}")
        h = w["hwnd"]
    else:
        h = user32.GetForegroundWindow()
        if not h:
            raise LookupError("no foreground window")
    return h, st["iuia"].ElementFromHandleBuildCache(h, st["cr"])


def _active_window_text(max_chars: int, max_depth: int, contains: Optional[str],
                        title_contains: Optional[str], value_chars: int) -> str:
    h, root = _root_for(title_contains)
    l, t, r, b = _rect(h)
    lines = [f"Window '{_title(h)}' ({_proc_name(_pid(h))}) hwnd=0x{h:X} rect={l},{t},{r},{b}"]
    used = len(lines[0])
    needle = contains.lower() if contains else None
    nodes, stopped = 0, ""
    for el, info, depth, shown in _walk(root, max_depth, seconds=8.0):
        if el is None:
            stopped = "time limit (8 s)"
            break
        nodes += 1
        if not info["show"]:
            continue
        if info["type"] in _TEXTY and not info["value"] and not info["password"]:
            info["value"] = _doc_text(el, value_chars)
        line = " " * shown + _fmt(info, value_chars)
        if needle and needle not in line.lower():
            continue
        if used + len(line) + 1 > max_chars - 110:  # leave room for the truncation note
            stopped = f"max_chars={max_chars}"
            break
        lines.append(line)
        used += len(line) + 1
    if stopped:
        lines.append(f"...[truncated: {stopped}, {nodes} controls read; narrow with contains= or raise max_chars]")
    elif needle and len(lines) == 1:
        lines.append(f"(no control line contains {contains!r}; {nodes} controls read)")
    return "\n".join(lines)


def _click_point(x: int, y: int, button: str, double: bool) -> None:
    _pg().click(x, y, clicks=2 if double else 1, interval=0.08, button=button)


def _click_element(name: str, control_type: Optional[str], index: int, double: bool, button: str,
                   title_contains: Optional[str]) -> str:
    if button not in ("left", "right", "middle"):
        return "ERROR: button must be left, right or middle"
    if title_contains:
        w, _ = _find_window(title_contains)
        if not w:
            return f"ERROR: no window title or process contains {title_contains!r}"
        if not _activate(w["hwnd"]):
            return f"ERROR: could not bring {_wdesc(w)} to the foreground"
    root_hwnd, root = _root_for(None)
    want = name.strip().lower()
    ctype = None
    if control_type:
        ctype = control_type.strip().lower()
        ctype = ctype[:-7] if ctype.endswith("control") else ctype
    exact, partial = [], []
    for el, info, _d, _s in _walk(root, max_depth=40, seconds=6.0):
        if el is None:
            break
        if ctype and info["type"].lower() != ctype:
            continue
        nm, aid = info["name"].strip().lower(), info["aid"].lower()
        l, t, r, b = info["rect"]
        info["usable"] = info["enabled"] and r > l and b > t
        if nm == want or (aid and aid == want):
            exact.append((el, info))
            if sum(1 for _e, i in exact if i["usable"]) > index:
                break
        elif want and want in nm:
            partial.append((el, info))
    kind, pool = "exact", [m for m in exact if m[1]["usable"]] or exact
    if not pool:
        kind, pool = "partial-name", [m for m in partial if m[1]["usable"]] or partial
    if not pool:
        return f"ERROR: no control named {name!r}" + (f" of type {control_type}" if control_type else "") + \
               f" in {_fg_desc()}. Read active_window_text to see the real names."
    if index >= len(pool):
        return f"ERROR: only {len(pool)} match(es) for {name!r}; index {index} is out of range"
    el, info = pool[index]
    desc = _fmt(info, 60)
    if info["usable"]:
        l, t, r, b = info["rect"]
        cx, cy = (l + r) // 2, (t + b) // 2
        under = user32.WindowFromPoint(wintypes.POINT(cx, cy))
        if not under or _pid(under) != _pid(root_hwnd):
            return (f"ERROR: did not click {desc}: the point ({cx},{cy}) is covered by another app's window "
                    f"({_title(user32.GetAncestor(under, 2)) if under else 'none'!r}); foreground: {_fg_desc()}")
        _click_point(cx, cy, button, double)
        time.sleep(0.15)
        return f"clicked {desc} at ({cx},{cy}) [{kind} match, {len(pool)} candidate(s)]; foreground: {_fg_desc()}"
    # Not on screen: fall back to UI Automation patterns (left click only).
    core = _uia_state["core"]
    for pid, iface, method in ((PAT_INVOKE, core.IUIAutomationInvokePattern, "Invoke"),
                               (PAT_SELECTIONITEM, core.IUIAutomationSelectionItemPattern, "Select"),
                               (PAT_TOGGLE, core.IUIAutomationTogglePattern, "Toggle")):
        p = _pattern(el, pid, iface)
        if p is not None and button == "left":
            getattr(p, method)()
            return f"{method}d {desc} via UI Automation (it has no on-screen rectangle); foreground: {_fg_desc()}"
    return f"ERROR: {desc} has no on-screen rectangle and supports no invoke/select/toggle pattern"


# ---------------------------------------------------------------------------------------------
# Screen capture, OCR, screenshot
# ---------------------------------------------------------------------------------------------
def _grab(x, y, w, h):
    import mss
    given = [v is not None for v in (x, y, w, h)]
    factory = getattr(mss, "MSS", None) or mss.mss
    with factory() as sct:
        if not any(given):
            mons = sct.monitors[1:]
            mon = next((m for m in mons if m["left"] == 0 and m["top"] == 0), mons[0])
        elif all(given):
            if w <= 0 or h <= 0:
                raise ValueError("w and h must be positive")
            mon = {"left": int(x), "top": int(y), "width": int(w), "height": int(h)}
        else:
            raise ValueError("give all of x, y, w, h (a region) or none of them (whole primary screen)")
        shot = sct.grab(mon)
    return shot, int(mon["left"]), int(mon["top"])


_ocr_engine = None


def _ocr(x, y, w, h, min_score: float) -> str:
    global _ocr_engine
    import numpy as np
    t0 = time.monotonic()
    shot, left, top = _grab(x, y, w, h)
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR
        # Screen text is upright and already sharp: detect at native size (the default upscales small
        # regions so their short side is 736 px, which costs ~1 s) and skip the angle classifier.
        _ocr_engine = RapidOCR(det_limit_type="max", det_limit_side_len=3840)
    img = np.ascontiguousarray(np.asarray(shot)[:, :, :3])  # BGRA -> BGR
    result, _ = _ocr_engine(img, use_cls=False)
    items = []
    for box, text, score in result or []:
        text = str(text).strip()
        if float(score) < min_score or not text:
            continue
        if len(text) == 1 and "⺀" <= text <= "鿿":
            continue  # a lone CJK glyph on an English UI is almost always an icon misread as text
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        bx, by = int(min(xs)) + left, int(min(ys)) + top
        items.append((bx, by, int(max(xs) - min(xs)), int(max(ys) - min(ys)), text))
    # Reading order: group boxes into rows by their top edge, then left to right.
    items.sort(key=lambda i: (i[1], i[0]))
    rows: list[list] = []
    for it in items:
        if rows and abs(it[1] - rows[-1][0][1]) <= max(6, it[3] // 2):
            rows[-1].append(it)
        else:
            rows.append([it])
    lines = [f"OCR of region {left},{top} {shot.width}x{shot.height}: {len(items)} text boxes "
             f"({time.monotonic() - t0:.1f}s). Format: x,y,w,h text (screen px; centre = x+w/2, y+h/2)"]
    for row in rows:
        for bx, by, bw, bh, text in sorted(row):
            lines.append(f"{bx},{by},{bw},{bh} {text}")
    return "\n".join(lines)


def _screenshot(scale: float, x, y, w, h):
    from PIL import Image as PILImage
    shot, left, top = _grab(x, y, w, h)
    img = PILImage.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    scale = min(max(float(scale), 0.05), 1.0)
    if scale < 1.0:
        img = img.resize((max(1, round(shot.width * scale)), max(1, round(shot.height * scale))),
                         PILImage.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=6)
    info = {"left": left, "top": top, "sx": shot.width / img.width, "sy": shot.height / img.height,
            "scale": scale, "img_w": img.width, "img_h": img.height, "ts": time.time()}
    global _last_shot
    _last_shot = info
    try:
        LAST_SHOT_FILE.write_text(json.dumps(info), encoding="utf-8")
    except OSError:
        pass
    sw, sh = _primary_size()
    note = (f"Screenshot {img.width}x{img.height} px = region {left},{top} {shot.width}x{shot.height} of the real "
            f"screen (primary {sw}x{sh}) at scale {scale:g}. Real coords: x = {left} + img_x*{info['sx']:.4g}, "
            f"y = {top} + img_y*{info['sy']:.4g}; or call click(x, y, space='screenshot') with image coords.")
    return [Image(data=buf.getvalue(), format="png"), note]


_last_shot: Optional[dict] = None


def _load_last_shot() -> Optional[dict]:
    if _last_shot:
        return _last_shot
    try:
        return json.loads(LAST_SHOT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------------------------
def _focus_window(title_contains: str) -> str:
    w, hits = _find_window(title_contains)
    if not w:
        return f"ERROR: no visible window title or process contains {title_contains!r}. Use list_windows."
    ok = _activate(w["hwnd"])
    time.sleep(0.15)
    more = f" ({len(hits)} matches; picked the top one)" if len(hits) > 1 else ""
    if ok:
        return f"focused {_wdesc(w)}{more}"
    return f"ERROR: Windows refused to bring {_wdesc(w)} to the foreground{more}; foreground is {_fg_desc()}"


_URI_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]+:")  # ms-settings:, https://, shell:..., not C:\
START_APPS_FILE = LOGS / "start_apps.json"  # Get-StartApps takes ~2 s, and the CLI restarts us every run


def _start_apps(max_age: float = 86400) -> list[dict]:
    try:
        cached = json.loads(START_APPS_FILE.read_text(encoding="utf-8"))
        if time.time() - cached["ts"] < max_age:
            return cached["apps"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
          "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress")
    proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                          stdin=subprocess.DEVNULL, capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
    data = json.loads(proc.stdout.decode("utf-8", "replace") or "[]")
    apps = [data] if isinstance(data, dict) else list(data)
    _safe(lambda: START_APPS_FILE.write_text(json.dumps({"ts": time.time(), "apps": apps}), encoding="utf-8"))
    return apps


_APP_ALIASES = {"vscode": "visual studio code", "vs code": "visual studio code", "explorer": "file explorer",
                "calc": "calculator", "cmd": "command prompt", "task manager": "task manager"}


def _match_start_app(query: str, max_age: float = 86400) -> tuple[Optional[dict], int, list[str]]:
    q = query.strip().lower()
    q = _APP_ALIASES.get(q, q)
    scored = []
    for app in _start_apps(max_age):
        name = str(app.get("Name") or "")
        n = name.lower()
        if n == q:
            s = 100
        elif n.startswith(q):
            s = 80
        elif any(word.startswith(q) for word in re.split(r"[\s\-_.()]+", n)):
            s = 70
        elif q in n or q.replace(" ", "") in n.replace(" ", ""):
            s = 50
        else:
            continue
        scored.append((-s, len(name), name, app))
    scored.sort(key=lambda t: t[:3])
    if not scored:
        return None, 0, []
    return scored[0][3], -scored[0][0], [t[2] for t in scored[:6]]


def _in_app_paths(name: str) -> bool:
    import winreg
    exe = name if name.lower().endswith(".exe") else name + ".exe"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            winreg.CloseKey(winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"))
            return True
        except OSError:
            continue
    return False


def _open_app(name_or_path: str, wait_seconds: float) -> str:
    target = os.path.expandvars(name_or_path.strip().strip('"'))
    if not target:
        return "ERROR: empty app name"
    before = {w["hwnd"] for w in _windows()}
    how = ""
    if _URI_RE.match(target) or Path(target).exists():
        os.startfile(target)
        how = f"opened {target!r}"
    else:
        app, score, names = _match_start_app(target)
        if not app and not shutil.which(target) and not _in_app_paths(target):
            # Maybe installed since the cached list was written: refresh it if it is over 5 minutes old.
            app, score, names = _match_start_app(target, max_age=300)
        if app and score >= 100:
            os.startfile("shell:AppsFolder\\" + app["AppID"])
            how = f"launched Start-menu app '{app['Name']}'"
        elif shutil.which(target) or _in_app_paths(target):
            os.startfile(target)
            how = f"launched {target!r} (PATH / App Paths)"
        elif app:
            os.startfile("shell:AppsFolder\\" + app["AppID"])
            how = f"launched Start-menu app '{app['Name']}' (closest match; others: {', '.join(names[1:]) or 'none'})"
        else:
            try:
                os.startfile(target)
                how = f"launched {target!r} via the shell"
            except OSError as e:
                return f"ERROR: no app, path or Start-menu entry matches {name_or_path!r} ({e})"
    end = time.monotonic() + max(0.0, wait_seconds)
    new = None
    while time.monotonic() < end:
        time.sleep(0.3)
        fresh = [w for w in _windows() if w["hwnd"] not in before]
        if fresh:
            new = next((w for w in fresh if w["fg"]), fresh[0])
            time.sleep(0.5)  # let it finish creating its UI / final title
            break
    if not new:
        return f"{how}. No new window within {wait_seconds:g}s (it may reuse an existing window); foreground: {_fg_desc()}"
    activated = _activate(new["hwnd"])
    new["title"] = _title(new["hwnd"]) or new["title"]
    return f"{how}. New window {_wdesc(new)}" + ("" if activated else f" (not foreground; foreground is {_fg_desc()})")


def _click(x: float, y: float, button: str, double: bool, space: str) -> str:
    if button not in ("left", "right", "middle"):
        return "ERROR: button must be left, right or middle"
    if space == "screenshot":
        shot = _load_last_shot()
        if not shot:
            return "ERROR: no screenshot taken yet, so screenshot coordinates cannot be mapped"
        rx, ry = round(shot["left"] + x * shot["sx"]), round(shot["top"] + y * shot["sy"])
    elif space == "screen":
        rx, ry = round(x), round(y)
    else:
        return "ERROR: space must be 'screen' or 'screenshot'"
    _click_point(rx, ry, button, double)
    time.sleep(0.15)
    kind = ("double-" if double else "") + button
    return f"{kind}-clicked screen ({rx},{ry}); foreground: {_fg_desc()}"


def _paste(text: str, target) -> bool:
    import pyperclip
    old = _safe(pyperclip.paste)
    pyperclip.copy(text.replace("\n", "\r\n"))
    time.sleep(0.05)
    try:
        if user32.GetForegroundWindow() != target:
            return False
        _pg().hotkey("ctrl", "v")
        time.sleep(0.4)  # some apps read the clipboard lazily
        return True
    finally:
        if old:
            _safe(lambda: pyperclip.copy(old))


def _focused_text(limit: int = 20000) -> Optional[str]:
    """Text of the focused control (Value or Text pattern), or None if unreadable / a password box."""
    st = _uia()
    el = _safe(lambda: st["iuia"].GetFocusedElement())
    if not el or _safe(lambda: el.GetCurrentPropertyValue(P_PASSWORD), False):
        return None
    v = _safe(lambda: el.GetCurrentPropertyValue(P_VALUE))
    if isinstance(v, str) and v:
        return v
    tp = _pattern(el, PAT_TEXT, st["core"].IUIAutomationTextPattern)
    return _safe(lambda: tp.DocumentRange.GetText(limit)) if tp is not None else None


def _type_text(text: str, interval: float, title_contains: Optional[str]) -> str:
    if title_contains:
        w, _ = _find_window(title_contains)
        if not w:
            return f"ERROR: no window title or process contains {title_contains!r}; nothing typed"
        if not _activate(w["hwnd"]):
            return f"ERROR: could not bring {_wdesc(w)} to the foreground; nothing typed"
    target = user32.GetForegroundWindow()
    text = text.replace("\r\n", "\n")
    if not text:
        return "nothing to type"
    pg = _pg()
    pg.failSafeCheck()
    plain = all(c in "\n\t" or 32 <= ord(c) < 127 for c in text)
    if plain and len(text) <= 300:
        for i, ch in enumerate(text):
            # Stop the moment focus leaves the target, so keys never land in another window.
            if user32.GetForegroundWindow() != target:
                return f"STOPPED after {i} of {len(text)} chars: focus moved to {_fg_desc()}"
            if i and i % 20 == 0:
                if STOP_FILE.exists():
                    return f"STOPPED by the kill switch after {i} of {len(text)} chars"
                pg.failSafeCheck()
            _send_char(ch)
            if interval > 0:
                time.sleep(interval)
        how = "keystrokes"
    else:
        if not _paste(text, target):
            return f"STOPPED: focus moved to {_fg_desc()} before pasting; nothing typed"
        how = "clipboard paste (text clipboard restored)"
    time.sleep(0.15)
    result = f"typed {len(text)} chars via {how} into {_fg_desc()}"
    field = _focused_text()
    if field is not None:
        if " ".join(text.split()) in " ".join(field.split()):
            result += "; verified in the focused field"
        else:
            result += (f"; WARNING: the focused field does not contain the typed text (keys dropped or "
                       f"autocorrected?). It ends with: '{_clip(field[-120:], 120)}'")
    return result


_KEY_ALIASES = {"control": "ctrl", "windows": "win", "cmd": "win", "super": "win", "meta": "win",
                "escape": "esc", "return": "enter", "del": "delete", "ins": "insert", "pgup": "pageup",
                "pgdn": "pagedown", "option": "alt", "spacebar": "space", "bksp": "backspace"}


def _parse_combo(combo: str) -> list[str]:
    trailing_plus = combo.endswith("++") or combo == "+"
    body = combo[:-2] if combo.endswith("++") else ("" if combo == "+" else combo)
    keys = [k for k in body.split("+") if k] + (["+"] if trailing_plus else [])
    return [_KEY_ALIASES.get(k.lower(), k.lower()) for k in keys]


def _hotkey(keys: str, title_contains: Optional[str] = None) -> str:
    pg = _pg()
    target = None
    if title_contains:
        w, _ = _find_window(title_contains)
        if not w:
            return f"ERROR: no window title or process contains {title_contains!r}; no keys pressed"
        if not _activate(w["hwnd"]):
            return f"ERROR: could not bring {_wdesc(w)} to the foreground; no keys pressed"
        target = w["hwnd"]
    norm = re.sub(r"\s*\+\s*", "+", keys.strip())
    combos = [c for c in re.split(r"[,\s]+", norm) if c]
    if not combos:
        return "ERROR: no keys given"
    parsed = [_parse_combo(c) for c in combos]
    bad = sorted({k for ks in parsed for k in ks if k not in pg.KEYBOARD_KEYS})
    if bad or any(not ks for ks in parsed):
        return f"ERROR: unknown key(s) {bad}; use names like ctrl, alt, shift, win, enter, tab, esc, f5, a"
    for i, ks in enumerate(parsed):
        if STOP_FILE.exists():
            return f"STOPPED by the kill switch after {i} of {len(parsed)} key combos"
        if target and user32.GetForegroundWindow() != target:
            return f"STOPPED after {i} of {len(parsed)} key combos: focus moved to {_fg_desc()}"
        pg.hotkey(*ks, interval=0.03) if len(ks) > 1 else pg.press(ks[0])
        time.sleep(0.05)
    time.sleep(0.1)
    return f"pressed {', '.join('+'.join(ks) for ks in parsed)}; foreground: {_fg_desc()}"


def _scroll(amount: int, x, y, horizontal: bool) -> str:
    pg = _pg()
    if (x is None) != (y is None):
        return "ERROR: give both x and y, or neither (scrolls at the current mouse position)"
    if x is not None:
        pg.moveTo(round(x), round(y))
    else:
        pg.failSafeCheck()
    flag = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
    user32.mouse_event(flag, 0, 0, (int(amount) * WHEEL_DELTA) & 0xFFFFFFFF, 0)
    time.sleep(0.1)
    px, py = pg.position()
    way = ("right" if amount > 0 else "left") if horizontal else ("up" if amount > 0 else "down")
    return f"scrolled {abs(int(amount))} notch(es) {way} at ({px},{py})"


# ---------------------------------------------------------------------------------------------
# Plumbing: one worker thread (COM apartment + serialised desktop input), stop file, action log
# ---------------------------------------------------------------------------------------------
def _thread_init() -> None:
    ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED, for UIA and the shell


_EXEC = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jarvis-desktop", initializer=_thread_init)
_log_lock = threading.Lock()


async def _call(fn, **kwargs):
    return await asyncio.get_running_loop().run_in_executor(_EXEC, functools.partial(fn, **kwargs))


async def _read(fn, **kwargs):
    try:
        return await _call(fn, **kwargs)
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


def _log_action(tool: str, args: dict, result: str) -> None:
    def short(v):
        return v[:500] + "..." if isinstance(v, str) and len(v) > 500 else v
    rec = {"ts": datetime.now().isoformat(timespec="milliseconds"), "tool": tool,
           "args": {k: short(v) for k, v in args.items()}, "result": short(result)}
    try:
        with _log_lock:
            LOGS.mkdir(parents=True, exist_ok=True)
            with open(ACTION_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"jarvis-desktop: could not write {ACTION_LOG}: {e}", file=sys.stderr)


async def _act(tool: str, fn, **kwargs) -> str:
    if STOP_FILE.exists():
        result = STOP_MSG
    else:
        try:
            result = await _call(fn, **kwargs)
        except Exception as e:
            if type(e).__name__ == "FailSafeException":
                result = ("ABORTED by the pyautogui fail-safe: the mouse is in a screen corner. "
                          "Move it away from the corner to allow actions again.")
            else:
                result = f"ERROR: {type(e).__name__}: {e}"
    _log_action(tool, kwargs, result)
    return result


# ---------------------------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------------------------
mcp = _Server(
    "jarvis-desktop",
    instructions=("Windows desktop control. Coordinates are real screen pixels. Read cheaply: "
                  "active_window_text first, then ocr_screen, screenshot last. Actions are refused "
                  "while the kill switch file logs\\STOP exists."),
)


def _tool(fn):
    try:
        return mcp.tool(structured_output=False)(fn)
    except TypeError:  # older mcp 1.x without structured_output
        return mcp.tool()(fn)


@_tool
async def list_windows() -> str:
    """List visible top-level windows, topmost first: handle, process, title, rect (left,top,right,bottom)."""
    def run():
        wins = _windows()
        lines = [f"{len(wins)} windows (topmost first)"]
        for w in wins:
            l, t, r, b = w["rect"]
            pos = "minimized" if w["minimized"] else f"rect={l},{t},{r},{b}"
            lines.append(f"0x{w['hwnd']:X} {w['proc']} '{w['title']}' {pos}" + (" FOREGROUND" if w["fg"] else ""))
        return "\n".join(lines)
    return await _read(run)


@_tool
async def active_window_text(max_chars: int = 4000, max_depth: int = 12, contains: Optional[str] = None,
                             title_contains: Optional[str] = None, value_chars: int = 300) -> str:
    """Cheapest way to read the screen: UI Automation tree of the foreground window, one line per control:
    Type 'Name' [AutomationId] value='...' rect=l,t,r,b. Indent = nesting. contains= keeps only lines with
    that text; title_contains= reads another window without focusing it; value_chars caps each value."""
    return await _read(_active_window_text, max_chars=max_chars, max_depth=max_depth, contains=contains,
                       title_contains=title_contains, value_chars=value_chars)


@_tool
async def ocr_screen(x: Optional[int] = None, y: Optional[int] = None, w: Optional[int] = None,
                     h: Optional[int] = None, min_score: float = 0.5) -> str:
    """OCR text lines with boxes (x,y,w,h in screen px) for the whole primary screen or a region.
    Use when active_window_text shows no useful text (games, canvases, images, remote desktops)."""
    return await _read(_ocr, x=x, y=y, w=w, h=h, min_score=min_score)


@_tool
async def screenshot(scale: float = 0.5, x: Optional[int] = None, y: Optional[int] = None,
                     w: Optional[int] = None, h: Optional[int] = None):
    """PNG of the primary screen (or a region), downscaled by scale. Most expensive read: use last.
    The note gives the mapping back to screen px; click(..., space='screenshot') does it for you."""
    return await _read(_screenshot, scale=scale, x=x, y=y, w=w, h=h)


@_tool
async def focus_window(title_contains: str) -> str:
    """Bring a window to the front (restoring it if minimized). Matches title substring, then process
    name, or an exact handle like 0x1A2B."""
    return await _act("focus_window", _focus_window, title_contains=title_contains)


@_tool
async def open_app(name_or_path: str, wait_seconds: float = 6.0) -> str:
    """Launch an app like the Start menu does (e.g. 'notepad', 'Spotify', 'Visual Studio Code'), or open a
    path / URL / URI (e.g. 'ms-settings:'). Waits for its new window and reports it."""
    return await _act("open_app", _open_app, name_or_path=name_or_path, wait_seconds=wait_seconds)


@_tool
async def click(x: float, y: float, button: str = "left", double: bool = False, space: str = "screen") -> str:
    """Click at x,y. space='screen' (real px) or 'screenshot' (pixel coords in the last screenshot image)."""
    return await _act("click", _click, x=x, y=y, button=button, double=double, space=space)


@_tool
async def click_element(name: str, control_type: Optional[str] = None, index: int = 0, double: bool = False,
                        button: str = "left", title_contains: Optional[str] = None) -> str:
    """Click a control in the foreground window by its UI Automation Name (or AutomationId), e.g.
    name='Save', control_type='Button'. Exact matches win over partial; index picks among several.
    title_contains= focuses that window first."""
    return await _act("click_element", _click_element, name=name, control_type=control_type, index=index,
                      double=double, button=button, title_contains=title_contains)


@_tool
async def type_text(text: str, interval: float = 0.01, title_contains: Optional[str] = None) -> str:
    """Type text into the focused control (\\n = Enter). Non-ASCII or long text is pasted via the clipboard.
    title_contains= focuses that window first and refuses if it cannot."""
    return await _act("type_text", _type_text, text=text, interval=interval, title_contains=title_contains)


@_tool
async def hotkey(keys: str, title_contains: Optional[str] = None) -> str:
    """Press a key combo such as 'ctrl+s', 'alt+tab', 'win+r', 'enter', 'f5'. Several: 'ctrl+a, ctrl+c'.
    title_contains= focuses that window first and refuses if it cannot."""
    return await _act("hotkey", _hotkey, keys=keys, title_contains=title_contains)


@_tool
async def scroll(amount: int, x: Optional[int] = None, y: Optional[int] = None, horizontal: bool = False) -> str:
    """Scroll the mouse wheel by amount notches (positive = up/right, negative = down/left), at x,y if given."""
    return await _act("scroll", _scroll, amount=amount, x=x, y=y, horizontal=horizontal)


# ---------------------------------------------------------------------------------------------
def _guard_stdout_v1() -> None:
    """mcp 1.x writes the protocol to sys.stdout.buffer and does not shield it from stray prints.
    Keep the real pipe for the protocol only and send every other write (Python or C) to stderr."""
    try:
        wire = os.fdopen(os.dup(1), "wb")
        os.dup2(2, 1)
    except OSError:
        return

    class _Stdout:
        buffer = wire
        encoding = "utf-8"

        def write(self, s):
            return sys.__stderr__.write(s)

        def flush(self):
            sys.__stderr__.flush()

        def isatty(self):
            return False

    sys.stdout = _Stdout()


def main() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    if not _MCP_V2:
        _guard_stdout_v1()  # mcp 2.x does the same diversion itself
    mcp.run("stdio")


if __name__ == "__main__":
    main()
