"""Start JARVIS automatically when you log in (it then waits for "Hey Jarvis").

Installs a shortcut in the Windows Startup folder that runs `Start JARVIS.vbs`
(windowless, via pythonw). No admin rights needed; it only touches your own
Startup folder.

    python -m jarvis.main --install-autostart
    python -m jarvis.main --uninstall-autostart
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .config import ROOT

SHORTCUT_NAME = "JARVIS.lnk"


def _startup_dir() -> Path:
    appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    return _startup_dir() / SHORTCUT_NAME


def is_installed() -> bool:
    return shortcut_path().exists()


def install() -> Path:
    """Create the Startup shortcut pointing at Start JARVIS.vbs. Returns its path."""
    target_vbs = ROOT / "Start JARVIS.vbs"
    if not target_vbs.exists():
        raise FileNotFoundError(f"{target_vbs} not found")
    link = shortcut_path()
    link.parent.mkdir(parents=True, exist_ok=True)
    # Build the .lnk with WScript.Shell via a short PowerShell one-liner.
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
        "$s.TargetPath='{wscript}';"
        "$s.Arguments='\"{vbs}\"';"
        "$s.WorkingDirectory='{root}';"
        "$s.IconLocation='{wscript},0';"
        "$s.Description='JARVIS personal assistant';"
        "$s.Save()"
    ).format(
        link=str(link),
        wscript=os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "wscript.exe"),
        vbs=str(target_vbs),
        root=str(ROOT),
    )
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   check=True, capture_output=True)
    return link


def uninstall() -> bool:
    link = shortcut_path()
    if link.exists():
        link.unlink()
        return True
    return False
