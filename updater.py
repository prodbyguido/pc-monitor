"""Check GitHub for a newer PC Monitor and install it.

Only the packaged PC Monitor.exe updates itself. The developer copy (the one updates are made
from) never checks. The check is one small request to GitHub's public API asking for the
latest release; nothing about your PC is sent.
"""
import json
import os
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from config import APP_DIR, DEV_COPY, FROZEN
from version import GITHUB_REPO, VERSION

EXE_NAME = "PC Monitor.exe"


def can_check() -> bool:
    return bool(GITHUB_REPO) and not DEV_COPY


def _version_tuple(text: str) -> tuple:
    parts = []
    for p in text.lstrip("vV").split("."):
        digits = "".join(c for c in p if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def latest_release() -> dict | None:
    """{"version", "notes", "zip_url", "page"} if GitHub has a newer version, else None."""
    if not can_check():
        return None
    req = urllib.request.Request(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                                 headers={"User-Agent": "PC-Monitor", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.load(resp)
    tag = data.get("tag_name", "")
    if _version_tuple(tag) <= _version_tuple(VERSION):
        return None
    zip_url = next((a["browser_download_url"] for a in data.get("assets", [])
                    if a.get("name", "").lower().endswith(".zip")), None)
    return {"version": tag.lstrip("vV"), "notes": data.get("body") or "", "zip_url": zip_url,
            "page": data.get("html_url", f"https://github.com/{GITHUB_REPO}/releases/latest")}


def install(release: dict, progress=lambda text: None) -> bool:
    """Download the new version and hand over to a small script that swaps the files in once
    PC Monitor has closed, then starts it again. Returns True if the app should now quit.
    Only the program files are replaced; your settings and history live elsewhere."""
    if not FROZEN or not release.get("zip_url"):
        os.startfile(release["page"])  # not the packaged app: open the download page instead
        return False
    work = Path(tempfile.mkdtemp(prefix="pcmonitor_update_"))
    progress("Downloading…")
    zip_path = work / "update.zip"
    req = urllib.request.Request(release["zip_url"], headers={"User-Agent": "PC-Monitor"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(zip_path, "wb") as out:
        while chunk := resp.read(1024 * 1024):
            out.write(chunk)
    progress("Unpacking…")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(work / "new")
    new_exe = next((work / "new").rglob(EXE_NAME), None)
    if new_exe is None:
        raise RuntimeError("The download didn't contain PC Monitor.exe")
    script = work / "install_update.bat"
    script.write_text(
        "@echo off\r\n"
        "rem Wait for PC Monitor to close, copy in the new version, start it again.\r\n"
        ":wait\r\n"
        f'tasklist /fi "imagename eq {EXE_NAME}" | find /i "{EXE_NAME}" >nul && '
        "(timeout /t 1 /nobreak >nul & goto wait)\r\n"
        f'robocopy "{new_exe.parent}" "{APP_DIR}" /E /R:5 /W:1 >nul\r\n'
        f'start "" "{APP_DIR / EXE_NAME}"\r\n', encoding="utf-8")
    subprocess.Popen(["cmd", "/c", str(script)], creationflags=0x08000000)
    progress("Restarting…")
    return True
