"""Where PC Monitor keeps things, and your settings (saved to settings.ini).

APP_DIR   The program's own files. For the packaged PC Monitor.exe, the folder the .exe is in.
DATA_DIR  Settings, history, logs and exports. Normally Windows' per-user app-data folder
          (%LOCALAPPDATA%\\PC Monitor), so installing an update never touches anyone's data.
          The developer copy (the one updates are made from, marked by a "developer_copy" file
          next to the code) keeps its data in its own folder and never checks for updates.
"""
import os
import sys
from dataclasses import dataclass, fields
from pathlib import Path

from PySide6.QtCore import QSettings

FROZEN = getattr(sys, "frozen", False)  # running as the packaged PC Monitor.exe
APP_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))  # bundled files like the icon
DEV_COPY = (not FROZEN and (APP_DIR / "developer_copy").exists()
            and not os.environ.get("PCMONITOR_AS_USER"))  # testing: behave like a friend's copy
if os.environ.get("PCMONITOR_DATA_DIR"):  # used when testing a build without touching real data
    DATA_DIR = Path(os.environ["PCMONITOR_DATA_DIR"])
elif DEV_COPY:
    DATA_DIR = APP_DIR
else:
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PC Monitor"
DATA_DIR.mkdir(parents=True, exist_ok=True)

SETTINGS_FILE = DATA_DIR / "settings.ini"
WINDOW_FILE = DATA_DIR / "window.ini"
LOG_DIR = DATA_DIR / "logs"
EXPORT_DIR = DATA_DIR / "exports"
CRASH_LOG = DATA_DIR / "crash.log"


@dataclass
class Settings:
    # Gaming
    fps_card: bool = True
    session_summary: bool = True
    peak_markers: bool = True
    # Alerts
    alerts: bool = True
    alert_seconds: int = 5
    alert_pulse: bool = True
    alert_notification: bool = True
    alert_sound: bool = False
    throttle_badges: bool = True
    # Looks
    smooth_animation: bool = True
    tray_icon: bool = True
    theme: str = "Dark"
    # Overlay & mini mode
    overlay: bool = False
    overlay_opacity: int = 85
    overlay_click_through: bool = True
    hotkeys: bool = True
    mini_x: int = -1
    mini_y: int = -1
    # History
    time_range: int = 120
    logging: bool = False
    # Tracking & reports (both off until you turn them on)
    app_tracker: bool = False
    net_watcher: bool = False
    # About your PC (used by the Wi-Fi score, game reports and the app tracker)
    internet_plan_mbps: int = 0   # 0 = not set
    my_apps: str = ""             # your own apps, e.g. "MyApp.exe, MyTool.exe"
    my_pc_notes: str = ""         # extra specs for game reports, e.g. cooler, monitors
    # Updates and first run
    update_check: bool = True
    first_run_done: bool = False

    @classmethod
    def load(cls) -> "Settings":
        store = QSettings(str(SETTINGS_FILE), QSettings.IniFormat)
        values = {}
        for f in fields(cls):
            if store.contains(f.name):
                try:
                    values[f.name] = store.value(f.name, type=type(f.default))
                except (TypeError, ValueError):
                    pass
        return cls(**values)

    def save(self):
        store = QSettings(str(SETTINGS_FILE), QSettings.IniFormat)
        for f in fields(self):
            store.setValue(f.name, getattr(self, f.name))
        store.sync()

    def my_app_names(self) -> set[str]:
        """Your apps as lowercase .exe names."""
        names = set()
        for part in self.my_apps.replace(";", ",").split(","):
            part = part.strip().lower()
            if part:
                names.add(part if part.endswith(".exe") else part + ".exe")
        return names
