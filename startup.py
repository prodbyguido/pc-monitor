"""Start PC Monitor when you log in, using a shortcut in your Windows Startup folder."""
import os
import subprocess
import sys
from pathlib import Path

from config import APP_DIR, FROZEN

STARTUP_DIR = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
SHORTCUT = STARTUP_DIR / "PC Monitor.lnk"


def enabled() -> bool:
    return SHORTCUT.exists()


def set_enabled(on: bool):
    if not on:
        SHORTCUT.unlink(missing_ok=True)
        return
    if FROZEN:
        target, args = sys.executable, ""
    else:  # running from the source code: start it without a console window
        venv = APP_DIR / ".venv" / "Scripts" / "pythonw.exe"
        target = str(venv if venv.exists() else Path(sys.executable).with_name("pythonw.exe"))
        args = f'"{APP_DIR / "app.py"}"'
    script = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{SHORTCUT}'); "
              f"$s.TargetPath = '{target}'; $s.Arguments = '{args}'; "
              f"$s.WorkingDirectory = '{APP_DIR}'; $s.Description = 'PC Monitor'; $s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], timeout=20,
                   creationflags=0x08000000)  # no console window
