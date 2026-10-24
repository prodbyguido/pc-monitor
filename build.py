"""Package PC Monitor as "PC Monitor.exe" and zip it for sharing.

Run with:  .venv\\Scripts\\python.exe build.py
Output:    dist\\PC-Monitor-<version>.zip  (a "PC Monitor" folder with the .exe inside)

Only program files go in. Nothing from your settings or history is included: those live in
files that aren't part of the program (and the build checks for them anyway).
"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from version import VERSION

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
APP = DIST / "PC Monitor"
PERSONAL = {"settings.ini", "window.ini", "history.db", "crash.log", "developer_copy"}

START_HERE = """PC Monitor {version}
==================

1. Move this "PC Monitor" folder somewhere permanent, e.g. Documents or C:\\Programs.
2. Double-click "PC Monitor.exe".
   Windows may say "Windows protected your PC" because the app isn't from a big company:
   click "More info", then "Run anyway".
3. The setup guide opens and walks you through installing HWiNFO (free) for temperatures.
4. Optional: Settings -> "Start PC Monitor when Windows starts".

Updates: PC Monitor checks for new versions when it starts. When there is one, click
"Update available" (top right) and then "Update now". Your settings and history are kept.

Your data (settings, history) is stored in %LOCALAPPDATA%\\PC Monitor and never leaves your PC.
"""


def main():
    shutil.rmtree(DIST, ignore_errors=True)
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "app.py",
        "--name", "PC Monitor", "--windowed", "--onedir", "--noconfirm", "--clean",
        "--icon", "assets/icon.ico", "--add-data", "assets;assets",
        "--distpath", str(DIST), "--workpath", str(ROOT / "build"),
        "--exclude-module", "PySide6.QtWebEngineCore", "--exclude-module", "PySide6.QtWebEngineWidgets",
    ], cwd=ROOT, check=True)
    leaked = [p for p in APP.rglob("*") if p.name in PERSONAL]
    if leaked:
        sys.exit(f"Refusing to package: personal files found: {leaked}")
    (APP / "START HERE.txt").write_text(START_HERE.format(version=VERSION), encoding="utf-8")
    zip_path = DIST / f"PC-Monitor-{VERSION}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in APP.rglob("*"):
            z.write(f, f.relative_to(DIST))
    size = zip_path.stat().st_size / 1024**2
    print(f"\nBuilt {zip_path.name} ({size:.0f} MB)")


if __name__ == "__main__":
    main()
