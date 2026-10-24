"""PC Monitor: a live, read-only hardware dashboard.

Run with run.bat (or: .venv\\Scripts\\pythonw.exe app.py).
Settings: the gear button or Ctrl+,  ·  F11 fullscreen  ·  Ctrl+Shift+S save window layout
"""
import ctypes
import faulthandler
import os
import re
import sys
import threading
import time
import traceback
import winsound
from dataclasses import dataclass

from PySide6.QtCore import QObject, QProcess, QRect, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QSizePolicy, QStackedWidget, QSystemTrayIcon,
                               QVBoxLayout, QWidget)
import pyqtgraph as pg

from alerts import AlertManager
from config import APP_DIR, CRASH_LOG, DEV_COPY, FROZEN, RESOURCE_DIR, WINDOW_FILE, Settings
from datalog import DataLogger
from hotkeys import GlobalHotkeys
from mini import MiniBar
from netwatch import NetWatcher, WifiPoller, ping_stats, wifi_strength
from reports import ReportsPage
from sensors import (GPU_HW_SLOWDOWN, GPU_POWER_CAP, GPU_THERMAL, Sampler, cpu_temp_reading,
                     foreground_app, foreground_process, is_cpu_temp, is_hwinfo_gpu, is_not_game,
                     read_hwinfo, set_my_apps)
from db import connect
from recap import RecapPage
from session import SessionTracker, latest_id, load as load_session, save as save_session
import updater
from settings_page import SettingsPage
from setup_page import SetupPage
from version import VERSION
from hardware import cpu_limits
from status import (COOL, CPU, CPU_TEMP, DRIVE_TEMP, GOOD, GPU_TEMP, configure_cpu,
                    HOT, OTHER_TEMP, WARM, busy_status, fps_status, headroom_status, temp_status,
                    wifi_status)
from theme import T, stylesheet
from tracker import AppSampler, AppTracker
from tray import Tray
from widgets import AnimatedLabel, GraphCard, Motion, PerCoreCard, SplitGraphCard

UPDATE_MS = 1000          # how often to sample
FRAME_MS = 50             # animation frame time (20 fps) when smooth animation is on
OPEN_ON_SECONDARY = True  # with no saved layout: open maximized on a non-primary monitor
RANGE_BUTTONS = (("2 min", 120), ("10 min", 600), ("1 hr", 3600))
HOTKEY_OVERLAY, HOTKEY_MINI = 1, 2
RECAP_AUTO_CLOSE_MS = 60_000  # recap closes itself after this when clicks pass through
GPU_ONLY_GRACE_S = 60         # a busy GPU keeps a game session going this long without game frames


def fmt_pct(v): return f"{v:.0f}%"
def fmt_pct1(v): return f"{v:.1f}%"
def fmt_temp(v): return f"{v:.1f} °C"
def fmt_temp0(v): return f"{v:.0f} °C"
def fmt_gb(v): return f"{v:.1f} GB"
def fmt_watts(v): return f"{v:.0f} W"
def fmt_fps(v): return f"{v:.0f} FPS"


def fmt_rate(mb_per_s: float) -> str:
    if mb_per_s >= 1000:
        return f"{mb_per_s / 1000:.2f} GB/s"
    if mb_per_s >= 1:
        return f"{mb_per_s:.1f} MB/s"
    return f"{mb_per_s * 1000:.0f} KB/s"


def fmt_mbit(mbit: float) -> str:
    if mbit >= 1000:
        return f"{mbit / 1000:.2f} Gbit/s"
    if mbit >= 1:
        return f"{mbit:.1f} Mbit/s"
    return f"{mbit * 1000:.0f} kbit/s"


# --- HWiNFO readings ----------------------------------------------------------
# Every value you tick in HWiNFO's Gadget tab gets its own card, marked with HWINFO_MARK,
# except the ones below that feed specific cards (CPU temp, FPS, throttle badges).
HWINFO_MARK = " *"
WAITING_FOR_HWINFO = "Waiting for HWiNFO"

# Friendlier names for some readings: (text in HWiNFO's sensor name, label) -> (title, footer)
HWINFO_NAMES = {
    ("CPU [", "CPU Package Power"): ("CPU power", "Whole CPU package"),
    # A motherboard sensor chip's "CPU" reading (the chip brand varies between motherboards)
    ("(Nuvoton", "CPU"): ("Socket temp", "Motherboard sensor"),
    ("(ITE", "CPU"): ("Socket temp", "Motherboard sensor"),
    ("(Fintek", "CPU"): ("Socket temp", "Motherboard sensor"),
}


def is_fps_reading(r) -> bool:
    return ("framerate" in r.label.lower() or r.sensor.upper().startswith("RTSS")
            or r.sensor.startswith("PresentMon"))


def is_throttle_reading(r) -> bool:
    label = r.label.lower()
    return "throttling" in label or "prochot" in label


def gets_own_card(r, gpu_from_hwinfo_readings: bool = False) -> bool:
    """HWiNFO readings that feed the fixed cards (CPU temp, FPS, GPU on non-NVIDIA cards) don't
    get a card of their own."""
    return not (is_cpu_temp(r) or is_fps_reading(r) or is_throttle_reading(r)
                or (gpu_from_hwinfo_readings and is_hwinfo_gpu(r)))


@dataclass
class Frames:
    """Frame rates for the current game.

    displayed: frames reaching the screen, including any made by frame generation.
    rendered:  frames the game itself makes (HWiNFO's PresentMon "Framerate (Presented)").
    """
    displayed: float | None = None
    rendered: float | None = None
    low: float | None = None      # 1% low, when the source provides one (RTSS)
    game: str | None = None
    process: str | None = None    # the game's .exe, lowercase, e.g. "rocketleague.exe"
    found: bool = False           # any frame-rate reading at all (even just the desktop)
    # Optional PresentMon timings (ms per frame), the best clues for what's holding FPS back
    frame_time: float | None = None
    gpu_busy: float | None = None
    cpu_busy: float | None = None

    @property
    def running(self) -> bool:
        return bool(self.displayed and self.displayed > 0)

    @property
    def multiplier(self) -> int | None:
        """Frame-generation factor, e.g. 2 for 2×. None when there's no frame generation."""
        if not (self.running and self.rendered):
            return None
        ratio = self.displayed / self.rendered
        return round(ratio) if ratio >= 1.5 else None

    @property
    def feel(self) -> float | None:
        """The rate that decides how responsive the game feels: rendered when known."""
        return self.rendered if self.rendered else self.displayed


def clean_game_name(process: str) -> str | None:
    """"FortniteClient-Win64-Shipping.exe" -> "Fortnite"."""
    if not process or is_not_game(process):
        return None
    name = process[:-4] if process.lower().endswith(".exe") else process
    name = name.split("-")[0]
    return name[:-6] if name.endswith("Client") and len(name) > 6 else name


def frame_readings(readings) -> Frames:
    """Prefers HWiNFO's built-in PresentMon (has both rates); falls back to RTSS."""
    f = Frames()
    timings = {}
    for r in readings or []:
        label = r.label.lower()
        if r.sensor.startswith("PresentMon") and label in ("frame time", "gpu busy", "cpu busy"):
            m = re.search(r"\[(.+?)\]", r.sensor)
            if m and clean_game_name(m.group(1)):
                timings[label] = r.value
            continue
        if "framerate" not in label:
            continue
        f.found = True
        if r.sensor.startswith("PresentMon"):
            m = re.search(r"\[(.+?)\]", r.sensor)
            game = clean_game_name(m.group(1)) if m else None
            if not game:
                continue  # PresentMon is measuring the desktop, not a game
            f.game = game
            f.process = m.group(1).lower()
            if "displayed" in label:
                f.displayed = r.value
            elif "presented" in label:
                f.rendered = r.value
        elif "1%" in label:
            f.low = r.value
        elif "%" not in label and f.displayed is None:
            f.displayed = r.value
    if f.displayed is None and f.rendered is not None:
        f.displayed = f.rendered
    if f.running:
        f.frame_time = timings.get("frame time")
        f.gpu_busy = timings.get("gpu busy")
        f.cpu_busy = timings.get("cpu busy")
    return f


def short_source(sensor: str) -> str:
    parts = sensor.split(": ")
    name = parts[1] if len(parts) > 1 else parts[0]
    return name.split(" (")[0]


def unit_fmt(unit: str):
    decimals = 0 if unit in ("RPM", "MHz", "%") else 1
    return lambda v: f"{v:.{decimals}f} {unit}".rstrip()


def hwinfo_card(reading) -> GraphCard:
    title, footer = next(
        (names for (part, label), names in HWINFO_NAMES.items()
         if part in reading.sensor and label == reading.label),
        (reading.label, short_source(reading.sensor)),
    )
    title = title.replace("Temperature", "Temp").replace("temperature", "temp")
    if reading.unit == "°C":
        scale = {"y_range": (20, 100)}
    elif reading.unit == "%":
        scale = {"y_range": (0, 100)}
    else:
        scale = {"min_top": {"W": 50, "RPM": 500}.get(reading.unit, 1)}
    card = GraphCard(title + HWINFO_MARK, **scale)
    card.source_footer = footer
    card.alert_name = title if reading.unit == "°C" else None
    return card


def hwinfo_status(reading) -> str:
    if reading.unit == "°C":
        limits = DRIVE_TEMP if reading.sensor.startswith("S.M.A.R.T.") else OTHER_TEMP
        return temp_status(reading.value, limits)
    if reading.label == "CPU Package Power":
        return busy_status(reading.value / CPU["power_w"] * 100)
    return GOOD


class UpdateSignals(QObject):
    """Carries update-check results from the background thread back to the window."""
    found = Signal(object)
    status = Signal(str)
    ready = Signal(bool)


class Dashboard(QWidget):
    def __init__(self, settings: Settings):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("PC Monitor")
        self.settings = settings
        self.sampler = Sampler()
        configure_cpu(cpu_limits(self.sampler.cpu_name))  # colours fit this PC's processor
        set_my_apps(settings.my_app_names())
        self.alerts = AlertManager()
        self.sessions = SessionTracker()
        self.logger = DataLogger()
        self.hotkeys = GlobalHotkeys()
        QApplication.instance().installNativeEventFilter(self.hotkeys)
        self.in_mini = False
        self.recap_page = None        # built the first time a recap is shown
        self.page_before_recap = None
        self.recap_shown_at = None
        self.pending_recap = None     # recap waiting for you to leave mini mode
        self.last_usage = {}          # latest per-app CPU/memory, for game-session samples
        self.last_game_frames = float("-inf")
        con = connect()
        self._saved_sessions = latest_id(con) is not None
        con.close()
        self.tracker = None   # AppTracker thread while the app tracker is on
        self.netwatch = None  # NetWatcher thread while the network watcher is on
        self.last_sample_t = time.monotonic()
        self.clock_hold_until = 0.0
        s = self.sampler
        gpu = s.gpu

        # Header
        heading = QLabel("PC Monitor", objectName="heading")
        # The CPU/GPU names get cut off rather than forcing the window wider.
        cpu_short = re.sub(r" \d+-Core Processor", "", s.cpu_name)
        sub = QLabel(f"{cpu_short}  ·  "
                     f"{gpu.name.replace('NVIDIA GeForce ', '')}", objectName="subheading")
        sub.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.clock = QLabel("", objectName="subheading")
        legend = QLabel(
            "&nbsp;&nbsp;".join(f'<span style="color:{c}">●</span> {name}'
                                for c, name in ((COOL, "Cool"), (GOOD, "Good"),
                                                (WARM, "Warm"), (HOT, "Hot")))
            + f"&nbsp;&nbsp;&nbsp;{HWINFO_MARK.strip()} HWiNFO",
            objectName="subheading")
        self.range_group = QButtonGroup(self)
        self.nav_group = QButtonGroup(self)
        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(heading)
        top.addSpacing(14)
        # Screen switcher: one window, three screens
        for label, page in (("Dashboard", "dashboard"), ("Reports", "reports"),
                            ("⚙ Settings", "settings")):
            button = QPushButton(label, checkable=True, objectName="nav")
            button.clicked.connect(lambda _=False, p=page: self.show_page(p))
            self.nav_group.addButton(button)
            button.page = page
            top.addWidget(button)
        top.addSpacing(14)
        top.addWidget(sub, stretch=1)
        self.hardware_label = sub
        top.addStretch()
        # Only shown on the Dashboard screen
        self.dashboard_only = [legend]
        for label, seconds in RANGE_BUTTONS:
            button = QPushButton(label, checkable=True)
            button.clicked.connect(lambda _=False, sec=seconds: self.set_time_range(sec))
            self.range_group.addButton(button, seconds)
            top.addWidget(button)
            self.dashboard_only.append(button)
        top.addSpacing(14)
        top.addWidget(legend)
        top.addSpacing(10)
        self.update_badge = QPushButton("Update available", objectName="updatebadge")
        self.update_badge.clicked.connect(self.open_settings)
        self.update_badge.hide()
        top.addWidget(self.update_badge)
        top.addWidget(self.clock)

        # Cards
        self.cpu = GraphCard("CPU usage", y_range=(0, 100))
        self.cpu_temp = GraphCard("CPU temp" + HWINFO_MARK, y_range=(20, 100))
        self.cores = PerCoreCard(s.logical_cores, s.physical_cores)
        self.gpu_use = GraphCard("GPU usage", y_range=(0, 100))
        self.gpu_temp = GraphCard("GPU temp", y_range=(20, 100))
        self.vram = GraphCard("VRAM", y_range=(0, gpu.vram_total_gb))
        self.gpu_power = GraphCard("GPU power", y_range=(0, gpu.power_limit_w))
        self.ram = GraphCard("Memory", y_range=(0, 32))
        self.disk = SplitGraphCard("Disk", ("Read", "Write"), min_top=5)
        self.disk.setToolTip("All drives combined")
        self.net = SplitGraphCard("Network", ("Download", "Upload"), min_top=1, footer=True)
        self.net.setToolTip("All network adapters combined")
        self.fps = SplitGraphCard("FPS" + HWINFO_MARK, ("Displayed", "Rendered"), min_top=60,
                                  footer=True)
        self.fps.setToolTip("Displayed: every frame that reaches your screen, including ones made "
                            "by frame generation.\nRendered: frames the game itself makes. This "
                            "decides how responsive it feels, so the card is coloured by it.")
        self.hwinfo_cards = {}  # (sensor, label) -> GraphCard
        self.apps = SplitGraphCard("App usage", ("Most CPU", "Most memory"), min_top=10)
        self.apps.use_notes()
        self.apps.setToolTip("What's using the most CPU and the most memory right now.\n"
                             "CPU: % of your whole CPU.  Memory: % of your total RAM.")
        self.app_sampler = AppSampler()
        self.wifi = GraphCard("Wi-Fi", y_range=(0, 100))
        self.wifi.no_peaks = True  # the footer needs the room; a "peak" strength isn't useful
        self.wifi.setToolTip(
            "Wi-Fi strength, 0-100%. 0% = no internet. 100% = strong signal and enough speed\n"
            "and steadiness for several people gaming online plus 4K streaming at once.\n"
            "Link speed counts up to your internet plan's speed (Settings → About your PC).\n"
            "Made from: signal 25%, link speed 25%, ping 20%, jitter 15%, packet loss 15%.\n"
            "One really bad part pulls the whole score down. Ping, jitter and loss need the\n"
            "network watcher (Settings → Tracking & reports).")
        self.wifi_poller = WifiPoller()
        self.wifi_poller.start()

        self.cpu.footer.setText(f"{s.physical_cores} cores / {s.logical_cores} threads")
        self.vram.footer.setText(f"of {gpu.vram_total_gb:.0f} GB")
        self.gpu_power.footer.setText(f"Power limit {gpu.power_limit_w:.0f} W")
        if gpu.error:  # not an NVIDIA card (or no driver): stats can come from HWiNFO instead
            for card in (self.gpu_use, self.gpu_temp, self.vram, self.gpu_power):
                card.footer.setText("Needs HWiNFO's GPU readings (Settings → Setup guide)")

        self.dashboard_page = QWidget()
        self.grid = QGridLayout(self.dashboard_page)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(12)
        for c in range(4):
            self.grid.setColumnStretch(c, 1)
        self._rows = 0
        # Reports and Settings screens are built the first time you open them.
        self.pages = QStackedWidget()
        self.pages.addWidget(self.dashboard_page)
        self.reports_page = None
        self.settings_page = None
        self.setup_page = None
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(12)
        root.addLayout(top)
        root.addWidget(self.pages, stretch=1)
        self.show_page("dashboard")

        self.add_hwinfo_cards(read_hwinfo())
        self.relayout()

        # Extra windows
        self.tray = Tray(self)
        self.mini = MiniBar(settings, self.toggle_mini)

        # Timers and shortcuts
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.frame_timer = QTimer(self)
        self.frame_timer.timeout.connect(self.animate)
        QShortcut(QKeySequence("Ctrl+,"), self, self.open_settings)

        # Updates (never on the developer copy): checked in the background a few seconds after start
        self.update_found = None
        self.update_signals = UpdateSignals()
        self.update_signals.found.connect(self._update_found)
        self.update_signals.status.connect(self._update_status)
        self.update_signals.ready.connect(self._update_ready)
        if settings.update_check and updater.can_check():
            QTimer.singleShot(5000, self.check_for_updates)

        self.apply_settings()
        self.tick()
        self.timer.start(UPDATE_MS)
        if not settings.first_run_done and not DEV_COPY:
            self.show_page("setup")  # first run: walk through HWiNFO setup

    # --- layout ---------------------------------------------------------------
    def cards(self):
        return [self.cpu, self.cpu_temp, self.cores, self.apps, self.gpu_use, self.gpu_temp,
                self.vram, self.gpu_power, self.ram, self.disk, self.net, self.wifi, self.fps,
                *self.hwinfo_cards.values()]

    def relayout(self):
        """Place the visible cards left-to-right, top-to-bottom, four per row."""
        placed = [(self.cpu, 1), (self.cpu_temp, 1), (self.cores, 1), (self.apps, 1),
                  (self.gpu_use, 1), (self.gpu_temp, 1), (self.vram, 1), (self.gpu_power, 1),
                  (self.ram, 1), (self.disk, 1), (self.net, 1), (self.wifi, 1)]
        if self.settings.fps_card:
            placed.append((self.fps, 1))
        placed += [(card, 1) for card in self.hwinfo_cards.values()]
        for card in self.cards():
            self.grid.removeWidget(card)
        self.fps.setVisible(self.settings.fps_card)
        slot = 0
        for card, span in placed:
            row, col = divmod(slot, 4)
            if col + span > 4:
                slot = (row + 1) * 4
                row, col = divmod(slot, 4)
            self.grid.addWidget(card, row, col, 1, span)
            slot += span
        rows = (slot + 3) // 4
        for r in range(max(rows, self._rows)):
            self.grid.setRowStretch(r, 1 if r < rows else 0)
        self._rows = rows
        self.all_series = [s for card in self.cards() for s in card.series_list()]

    def add_hwinfo_cards(self, readings) -> bool:
        added = False
        for r in readings or []:
            key = (r.sensor, r.label)
            if gets_own_card(r, self.sampler.gpu.handle is None) and key not in self.hwinfo_cards:
                card = hwinfo_card(r)
                card.show_peaks(self.settings.peak_markers)
                for series in card.series_list():
                    series.set_view(self.settings.time_range)
                self.hwinfo_cards[key] = card
                added = True
        return added

    # --- settings -------------------------------------------------------------
    def apply_settings(self, field: str | None = None):
        st = self.settings
        if field == "theme":
            self.restart()
            return
        Motion.smooth = st.smooth_animation
        if not st.smooth_animation:
            for series in self.all_series:
                series.glide(1)
        self.frame_timer.start(FRAME_MS if st.smooth_animation else 250)
        for card in self.cards():
            card.show_peaks(st.peak_markers)
            if not st.throttle_badges:
                card.set_badge(None)
            if not (st.alerts and st.alert_pulse):
                card.set_alerting(False)
        if not st.alerts:
            self.alerts.reset()
        self.tray.setVisible(st.tray_icon)
        if field in (None, "overlay", "overlay_opacity", "overlay_click_through"):
            self.apply_window_mode()
        if field in (None, "hotkeys"):
            self.hotkeys.unregister_all()
            if st.hotkeys:
                self.hotkeys.register(HOTKEY_OVERLAY, "O", self.toggle_overlay, "Ctrl+Alt+O")
                self.hotkeys.register(HOTKEY_MINI, "M", self.toggle_mini, "Ctrl+Alt+M")
        if field in (None, "time_range"):
            self.set_time_range(st.time_range)
        if not st.logging:
            self.logger.close()
        if not st.session_summary:
            self.sessions.reset()
        if field in (None, "fps_card"):
            self.relayout()
            self.mini.show_item("fps", st.fps_card)
        if field in (None, "app_tracker", "net_watcher"):
            self.apply_recorders()
        if field in (None, "my_apps"):
            set_my_apps(st.my_app_names())
        self.sync_ui()

    def apply_recorders(self):
        """Start or stop the background recorders to match Settings."""
        st = self.settings
        if st.app_tracker and self.tracker is None:
            self.tracker = AppTracker()
            self.tracker.start()
        elif not st.app_tracker and self.tracker is not None:
            self.tracker.stop()
            self.tracker = None
        if st.net_watcher and self.netwatch is None:
            self.netwatch = NetWatcher()
            self.netwatch.start()
        elif not st.net_watcher and self.netwatch is not None:
            self.netwatch.stop()
            self.netwatch = None
        self.net.footer.setVisible(st.net_watcher)
        # Signal % uses a Windows tool that shows the location icon: only while the watcher is on.
        self.wifi_poller.allow_signal = st.net_watcher
        if not st.net_watcher:
            self.net.set_badge(None)
        if self.reports_page and self.reports_page.isVisible():
            self.reports_page.load()

    def set_time_range(self, seconds: int):
        if self.settings.time_range != seconds:
            self.settings.time_range = seconds
            self.settings.save()
        button = self.range_group.button(seconds)
        if button:
            button.setChecked(True)
        for series in self.all_series:
            series.set_view(seconds)
        if self.settings_page:
            self.settings_page.sync()

    def apply_window_mode(self):
        st = self.settings
        on_top = st.overlay
        click_through = st.overlay and st.overlay_click_through
        opacity = st.overlay_opacity / 100 if st.overlay else 1.0
        flags_changed = (bool(self.windowFlags() & Qt.WindowStaysOnTopHint) != on_top
                         or bool(self.windowFlags() & Qt.WindowTransparentForInput) != click_through)
        if flags_changed:
            visible, maximized, full = self.isVisible(), self.isMaximized(), self.isFullScreen()
            geometry = self.geometry()
            self.setWindowFlag(Qt.WindowStaysOnTopHint, on_top)
            self.setWindowFlag(Qt.WindowTransparentForInput, click_through)
            if visible and not self.in_mini:
                if full:
                    self.showFullScreen()
                elif maximized:
                    self.showMaximized()
                else:
                    self.setGeometry(geometry)
                    self.show()
        self.setWindowOpacity(opacity)
        self.mini.setWindowOpacity(opacity)

    def sync_ui(self):
        self.tray.sync()
        if self.settings_page:
            self.settings_page.sync()

    def show_page(self, page: str):
        """Switch screens: "dashboard", "reports" or "settings"."""
        if page == "reports" and self.reports_page is None:
            self.reports_page = ReportsPage(self)
            self.pages.addWidget(self.reports_page)
        if page == "settings" and self.settings_page is None:
            self.settings_page = SettingsPage(self)
            self.pages.addWidget(self.settings_page)
            if self.update_found:
                self._update_found(self.update_found)
        if page == "setup" and self.setup_page is None:
            self.setup_page = SetupPage(self)
            self.pages.addWidget(self.setup_page)
        widget = {"dashboard": self.dashboard_page, "reports": self.reports_page,
                  "settings": self.settings_page, "recap": self.recap_page,
                  "setup": self.setup_page}[page]
        self.pages.setCurrentWidget(widget)
        for button in self.nav_group.buttons():
            button.setChecked(button.page == page)
        for w in self.dashboard_only:
            w.setVisible(page == "dashboard")
        # The CPU/GPU names only fit when the Dashboard's controls aren't in the top bar.
        self.hardware_label.setVisible(page != "dashboard")

    def current_page(self) -> str:
        current = self.pages.currentWidget()
        for name, page in (("reports", self.reports_page), ("settings", self.settings_page),
                           ("recap", self.recap_page), ("setup", self.setup_page)):
            if page is not None and current is page:
                return name
        return "dashboard"

    # --- updates (friends' copies only) ------------------------------------------
    def check_for_updates(self, manual: bool = False):
        if not updater.can_check():
            return
        if manual:
            self._update_status("Checking…")

        def work():
            try:
                release = updater.latest_release()
            except Exception:
                self.update_signals.status.emit("Couldn't reach GitHub to check for updates.")
                return
            if release:
                self.update_signals.found.emit(release)
            elif manual:
                self.update_signals.status.emit(f"You're up to date (version {VERSION}).")
        threading.Thread(target=work, daemon=True, name="update-check").start()

    def _update_found(self, release: dict):
        self.update_found = release
        self.update_badge.setText(f"Update available: {release['version']}")
        self.update_badge.show()
        if self.settings_page:
            self.settings_page.show_update_status(
                f"Version {release['version']} is available. Your settings and history are kept.",
                can_install=True)

    def _update_status(self, text: str):
        if self.settings_page:
            self.settings_page.show_update_status(text, can_install=bool(self.update_found))

    def install_update(self):
        release = self.update_found
        if not release:
            return

        def work():
            try:
                restart = updater.install(release, self.update_signals.status.emit)
            except Exception as exc:
                self.update_signals.status.emit(f"Update failed: {exc}")
                return
            self.update_signals.ready.emit(restart)
        threading.Thread(target=work, daemon=True, name="update-install").start()

    def _update_ready(self, restart: bool):
        if restart:
            self.close()  # the updater swaps the files in and starts the new version

    def open_reports(self):
        self.show_main()
        self.show_page("reports")

    def open_settings(self):
        self.show_main()
        self.show_page("settings")

    # --- modes ----------------------------------------------------------------
    def toggle_overlay(self):
        self.settings.overlay = not self.settings.overlay
        self.settings.save()
        self.apply_settings("overlay")

    def toggle_mini(self):
        self.in_mini = not self.in_mini
        if self.in_mini:
            self.mini.place(self)
            self.mini.show()
            self.hide()
        else:
            self.mini.hide()
            self.show_main()
            if self.pending_recap:  # a game ended while you were in mini mode
                session, self.pending_recap = self.pending_recap, None
                self.show_summary(session)
        self.sync_ui()

    def show_main(self):
        if self.in_mini:
            self.toggle_mini()
            return
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def reset_peaks(self):
        for card in self.cards():
            card.reset_peaks()

    def show_summary(self, session):
        """Show the game recap screen (fills the window; Close goes back where you were)."""
        if self.in_mini:  # the main window is hidden: show it when you come back
            self.pending_recap = session
            self.sync_ui()
            return
        if self.recap_page is None:
            self.recap_page = RecapPage(self)
            self.pages.addWidget(self.recap_page)
        if self.current_page() != "recap":
            self.page_before_recap = self.current_page()
        self.recap_page.show_session(session)
        self.show_page("recap")
        self.recap_shown_at = time.monotonic()
        if self.settings.overlay and self.settings.overlay_click_through:
            # You can't click Close while clicks pass through, so it closes itself.
            shown = self.recap_shown_at
            QTimer.singleShot(RECAP_AUTO_CLOSE_MS,
                              lambda: self.close_recap() if self.recap_shown_at == shown else None)
        self.sync_ui()

    def close_recap(self):
        if self.current_page() == "recap":
            self.show_page(self.page_before_recap or "dashboard")

    def show_session_id(self, session_id: int):
        con = connect()
        session = load_session(con, session_id)
        con.close()
        if session:
            self.show_main()
            self.show_summary(session)

    def show_last_summary(self):
        con = connect()
        last = latest_id(con)
        con.close()
        if last:
            self.show_session_id(last)

    @property
    def has_recap(self) -> bool:
        return self.sessions.last is not None or self._saved_sessions

    def notify(self, title: str, message: str):
        st = self.settings
        if st.alert_notification and st.tray_icon and self.tray.isVisible():
            self.tray.showMessage(title, message, QSystemTrayIcon.Warning, 8000)
        if st.alert_sound:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)

    # --- sampling ---------------------------------------------------------------
    def tick(self):
        st = self.settings
        self.last_sample_t = time.monotonic()
        s = self.sampler.sample()
        if self.last_sample_t >= self.clock_hold_until:
            self.clock.setText(time.strftime("%H:%M:%S"))
        watch = []  # (alert name, card, colour, value text) for hot warnings

        self.cpu.update(s.cpu_total, fmt_pct, headroom_status(s.cpu_total))
        self.cores.update(s.cpu_per_core, [headroom_status(v) for v in s.cpu_per_core])

        temp = cpu_temp_reading(s.hwinfo)
        cpu_throttling = False
        if temp:
            color = temp_status(temp.value, CPU_TEMP, s.cpu_total)
            self.cpu_temp.update(temp.value, fmt_temp, color)
            self.cpu_temp.footer.setText(f"{temp.label.replace('CPU ', '').strip('()')} sensor  ·  "
                                         f"limit {CPU['throttle_c']} °C")
            watch.append(("CPU temp", self.cpu_temp, color, fmt_temp(temp.value)))
            cpu_throttling = temp.value >= CPU["throttle_c"] or any(
                is_throttle_reading(r) and r.value >= 1 for r in s.hwinfo)
        else:
            self.cpu_temp.value.set_value(None)
            self.cpu_temp.footer.setText(f"{WAITING_FOR_HWINFO} (see Settings → Setup guide)")
        self.cpu_temp.set_badge("THERMAL THROTTLE" if st.throttle_badges and cpu_throttling else None)

        if self.add_hwinfo_cards(s.hwinfo):
            self.relayout()
        current = {(r.sensor, r.label): r for r in s.hwinfo or []}
        cpu_power = None
        for key, card in self.hwinfo_cards.items():
            r = current.get(key)
            if r:
                color = hwinfo_status(r)
                card.update(r.value, unit_fmt(r.unit), color)
                card.footer.setText(card.source_footer)
                if card.alert_name:
                    watch.append((card.alert_name, card, color, unit_fmt(r.unit)(r.value)))
                if r.label == "CPU Package Power":
                    cpu_power = r.value
            else:
                card.value.set_value(None)
                card.footer.setText(WAITING_FOR_HWINFO)

        frames = frame_readings(s.hwinfo)
        self.update_fps_card(frames)

        g = s.gpu
        if g:
            gpu = self.sampler.gpu
            if self.vram.series.y_range[1] != round(gpu.vram_total_gb, 1):
                self.vram.set_y_max(round(gpu.vram_total_gb, 1))  # e.g. learned from HWiNFO
            vram_pct = g["vram_used_gb"] / gpu.vram_total_gb * 100
            self.gpu_use.update(g["util"], fmt_pct, busy_status(g["util"]))
            self.gpu_use.footer.setText(f"Core clock {g['clock_mhz']} MHz" if g["clock_mhz"] else
                                        "From HWiNFO" if gpu.from_hwinfo else "")
            color = temp_status(g["temp"], GPU_TEMP, g["util"])
            self.gpu_temp.update(g["temp"], fmt_temp0, color)
            watch.append(("GPU temp", self.gpu_temp, color, fmt_temp0(g["temp"])))
            if g["fan_pct"] is not None:
                self.gpu_temp.footer.setText(f"Fan {g['fan_pct']}%")
            color = headroom_status(vram_pct)
            self.vram.update(g["vram_used_gb"], fmt_gb, color)
            self.vram.footer.setText(f"of {gpu.vram_total_gb:.0f} GB  ·  {vram_pct:.0f}%")
            watch.append(("VRAM", self.vram, color, f"{vram_pct:.0f}% full"))
            self.gpu_power.update(g["power_w"], fmt_watts,
                                  busy_status(g["power_w"] / gpu.power_limit_w * 100))
            if gpu.from_hwinfo:
                self.gpu_power.footer.setText("From HWiNFO")
            reasons = g["throttle"] if st.throttle_badges else 0
            if reasons & GPU_THERMAL:
                self.gpu_temp.set_badge("THERMAL THROTTLE", HOT)
            elif reasons & GPU_HW_SLOWDOWN:
                self.gpu_temp.set_badge("HW SLOWDOWN", WARM)
            else:
                self.gpu_temp.set_badge(None)
            self.gpu_power.set_badge("POWER LIMIT" if reasons & GPU_POWER_CAP else None, T.MUTED)

        if self.ram.series.y_range[1] != round(s.ram_total_gb, 1):
            self.ram.set_y_max(round(s.ram_total_gb, 1))
        color = headroom_status(s.ram_percent)
        self.ram.update(s.ram_used_gb, fmt_gb, color)
        self.ram.footer.setText(f"of {s.ram_total_gb:.1f} GB  ·  {s.ram_percent:.0f}%")
        watch.append(("Memory", self.ram, color, f"{s.ram_percent:.0f}% full"))

        self.disk.update((s.disk_read_mb, s.disk_write_mb), fmt_rate)
        self.net.update((s.net_down_mbit, s.net_up_mbit), fmt_mbit)
        if self.netwatch:
            ms = self.netwatch.latest.get("internet")
            self.net.footer.setText(f"Ping {'<1' if ms == 0 else f'{ms:.0f}'} ms" if ms is not None
                                    else "Ping —")
            self.net.set_badge(None if self.netwatch.internet_up else "OFFLINE", HOT)
        self.update_app_usage(s.ram_total_gb)
        self.update_wifi()

        self.check_alerts(watch)
        self.track_session(frames, s, g, temp, cpu_power, cpu_throttling)
        if st.logging:
            self.logger.write({
                "cpu_usage_pct": s.cpu_total, "cpu_temp_c": temp.value if temp else None,
                "cpu_power_w": cpu_power, "gpu_usage_pct": g["util"] if g else None,
                "gpu_temp_c": g["temp"] if g else None, "gpu_power_w": g["power_w"] if g else None,
                "vram_used_gb": g["vram_used_gb"] if g else None, "ram_used_gb": s.ram_used_gb,
                "disk_read_mb_s": s.disk_read_mb, "disk_write_mb_s": s.disk_write_mb,
                "net_down_mbit_s": s.net_down_mbit, "net_up_mbit_s": s.net_up_mbit,
                "fps_displayed": frames.displayed if frames.running else None,
                "fps_rendered": frames.rendered if frames.running else None,
                "game": frames.game,
            })
        self.update_mini_and_tray(s, temp, g, frames)

    def update_app_usage(self, ram_total_gb: float):
        """The apps using the most CPU and the most memory right now."""
        usage = self.app_sampler.sample()
        self.last_usage = usage
        if not usage:  # the very first reading has nothing to compare against yet
            self.apps.update((0, 0), fmt_pct1, T.MUTED, show=(None, None), notes=("", ""))
            return
        cpu_app, (cpu, _) = max(usage.items(), key=lambda kv: kv[1][0])
        mem_app, (_, mem) = max(usage.items(), key=lambda kv: kv[1][1])
        mem_pct = mem / (ram_total_gb * 1024**3) * 100
        self.apps.update((cpu, mem_pct), fmt_pct1, (headroom_status(cpu), headroom_status(mem_pct)),
                         notes=(cpu_app, f"{mem / 1024**3:.1f} GB  ·  {mem_app}"))

    def update_wifi(self):
        poller = self.wifi_poller
        watcher = self.netwatch
        if watcher:
            watcher.signal = poller.signal  # saved each minute for Reports → Network
        pings = ping_stats(list(watcher.recent)) if watcher else None
        result = wifi_strength(poller.status, pings, watcher.internet_up if watcher else None,
                               self.settings.internet_plan_mbps)
        score = result["score"]
        if score is None:
            self.wifi.value.set_value(None)
            self.wifi.footer.setText(result["reason"] if poller.checked else "Checking Wi-Fi…")
            return
        self.wifi.update(score, fmt_pct, wifi_status(score))
        if result["reason"]:
            self.wifi.footer.setText(result["reason"])
            return
        st = poller.status
        parts = [f"Signal {st['signal']:.0f}%" if st and st["signal"] is not None else None,
                 f"{min(st['rx_mbps'], st['tx_mbps']):.0f} Mbps link" if st and st["rx_mbps"] else None]
        if pings and pings["ping"] is not None:
            parts.append(f"±{pings['jitter']:.0f} ms")
        elif not watcher:
            parts.append("no ping (watcher off)")
        self.wifi.footer.setText("  ·  ".join(p for p in parts if p))

    def update_fps_card(self, f: Frames):
        if not f.running:
            self.fps.update((0, 0), fmt_fps, T.MUTED, show=(None, None))
            self.fps.footer.setText("No game running" if f.found
                                    else "Tick PresentMon's Framerate values in HWiNFO's Gadget tab")
            return
        rendered = f.rendered if f.rendered else f.displayed
        # Coloured by the rendered rate: that's what decides how responsive the game feels.
        self.fps.update((f.displayed, rendered), fmt_fps, fps_status(f.feel),
                        show=(f.displayed, f.rendered))
        parts = [f.game] if f.game else []
        if f.multiplier:
            parts.append(f"{f.multiplier}× frame gen")
        elif f.rendered:
            parts.append("No frame gen")
        if f.low:
            parts.append(f"1% low {f.low:.0f}")
        self.fps.footer.setText("  ·  ".join(parts))

    def check_alerts(self, watch):
        st = self.settings
        if not st.alerts:
            return
        now = time.monotonic()
        for name, card, color, value_text in watch:
            alerting, warn = self.alerts.update(name, color, now, st.alert_seconds)
            card.set_alerting(alerting and st.alert_pulse)
            if warn:
                self.notify(f"{name} is in the red", f"{name} has been at {value_text} for "
                                                     f"{st.alert_seconds}+ seconds.")

    def track_session(self, f: Frames, s, g, temp, cpu_power, cpu_throttling):
        if not self.settings.session_summary:
            return
        # A session starts only when the frame counter sees an actual game. A busy GPU on its own
        # never starts one (clipping tools, video encoding, etc.), but it keeps a game session
        # going if the frame counter briefly reports some other app mid-game. Without any
        # frame-counter data at all, the old busy-GPU guess is the only option.
        gpu_busy = SessionTracker.is_active(None, g["util"] if g else None)
        now = time.time()
        if f.running:
            self.last_game_frames = now
        # ...but only for a minute without game frames, so e.g. Medal encoding clips after you
        # quit can't keep a session going forever.
        recent_game = now - self.last_game_frames < GPU_ONLY_GRACE_S
        active = (f.running
                  or (gpu_busy and self.sessions.current is not None and recent_game)
                  or (gpu_busy and not f.found and foreground_app() is not None))
        name = (f.game or foreground_app()) if active else None
        sample = self.game_sample(f, s, g, temp, cpu_power, cpu_throttling, name) if active else None
        finished = self.sessions.update(active, sample, name)
        if finished:
            finished.meta = self.pc_facts(s.ram_total_gb)
            save_session(finished)
            self._saved_sessions = True
            self.show_summary(finished)

    def pc_facts(self, ram_total_gb: float) -> dict:
        """Facts about this PC saved with each game session (used by the recap and its export)."""
        smp = self.sampler
        return {"cpu_name": smp.cpu_name, "gpu_name": smp.gpu.name,
                "cores": smp.physical_cores, "threads": smp.logical_cores,
                "smt_off": smp.physical_cores == smp.logical_cores,
                "vram_total_gb": smp.gpu.vram_total_gb, "ram_total_gb": ram_total_gb,
                "power_limit_w": smp.gpu.power_limit_w}

    def game_sample(self, f: Frames, s, g, temp, cpu_power, cpu_throttling, game) -> dict:
        """One second of a game session: everything the recap's analysis looks at."""
        def r(v, digits=1):
            return None if v is None else round(v, digits)
        game_key = (game or "").lower()
        busy_apps = {app: r(cpu) for app, (cpu, _) in self.last_usage.items()
                     if cpu >= 1.5 and app != "PC Monitor" and app != "Windows desktop"
                     and not (game_key and app.lower().startswith(game_key[:8]))}
        ping = self.netwatch.latest.get("internet") if self.netwatch else None
        sample = {
            "t": round(time.time(), 1),
            "fps": r(f.displayed) if f.running else None, "rfps": r(f.rendered) if f.running else None,
            "ft": r(f.frame_time, 2), "gb": r(f.gpu_busy, 2), "cb": r(f.cpu_busy, 2),
            "cpu": r(s.cpu_total), "core": r(max(s.cpu_per_core)) if s.cpu_per_core else None,
            "ct": r(temp.value) if temp else None, "cp": r(cpu_power), "cthr": bool(cpu_throttling),
            "ram": r(s.ram_percent), "disk": r(s.disk_read_mb + s.disk_write_mb),
            "ping": ping, "lost": bool(self.netwatch and ping is None),
            "bg": busy_apps,
            # Tabbed out: some other window is in front of the game (its FPS isn't meaningful)
            "away": bool(f.running and f.process and foreground_process() != f.process),
        }
        if g:
            sample.update(gpu=g["util"], gt=g["temp"], gp=r(g["power_w"]), thr=g["throttle"],
                          vram=r(g["vram_used_gb"] / self.sampler.gpu.vram_total_gb * 100))
        if s.ram_percent >= 85 and self.last_usage:
            sample["memtop"] = max(self.last_usage.items(), key=lambda kv: kv[1][1])[0]
        return sample

    def update_mini_and_tray(self, s, temp, g, f: Frames):
        cpu_t = temp.value if temp else None
        cpu_color = temp_status(cpu_t, CPU_TEMP, s.cpu_total) if temp else T.MUTED
        gpu_t = g["temp"] if g else None
        gpu_color = temp_status(gpu_t, GPU_TEMP, g["util"]) if g else T.MUTED
        if self.in_mini:
            self.mini.set("cpu_temp", cpu_t, fmt_temp0, cpu_color)
            self.mini.set("gpu_temp", gpu_t, fmt_temp0, gpu_color)
            self.mini.set("fps", f.displayed if f.running else None, lambda v: f"{v:.0f}",
                          fps_status(f.feel) if f.running else T.MUTED)
            self.mini.set("cpu", s.cpu_total, fmt_pct, headroom_status(s.cpu_total))
            self.mini.set("gpu", g["util"] if g else None, fmt_pct,
                          busy_status(g["util"]) if g else T.MUTED)
        if self.tray.isVisible():
            parts = [f"CPU {cpu_t:.0f} °C" if cpu_t is not None else "CPU temp: waiting for HWiNFO"]
            if gpu_t is not None:
                parts.append(f"GPU {gpu_t:.0f} °C")
            if f.running:
                parts.append(f"{f.displayed:.0f} FPS" + (f" ({f.rendered:.0f} rendered)"
                                                         if f.multiplier else ""))
            self.tray.set_reading(f"{cpu_t:.0f}" if cpu_t is not None else None, cpu_color,
                                  "PC Monitor\n" + "  ·  ".join(parts))

    def animate(self):
        now = time.monotonic()
        if Motion.smooth:
            if self.isVisible() and not self.isMinimized() and self.current_page() == "dashboard":
                fraction = (now - self.last_sample_t) * 1000 / UPDATE_MS
                for series in self.all_series:
                    series.glide(fraction)
            for label in list(AnimatedLabel.instances):
                label.step()
        phase = int(now * 2) % 2 == 0
        for card in self.cards():
            card.pulse(phase)

    # --- window -----------------------------------------------------------------
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F11:
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
        elif event.key() == Qt.Key_Escape and self.isFullScreen():
            self.showNormal()
        elif event.key() == Qt.Key_Escape and self.current_page() == "recap":
            self.close_recap()
        elif event.key() == Qt.Key_Escape and self.current_page() == "setup":
            self.setup_page.finish()
        elif event.key() == Qt.Key_Escape:
            self.show_page("dashboard")
        elif event.key() == Qt.Key_S and event.modifiers() == (Qt.ControlModifier | Qt.ShiftModifier):
            self.save_layout()
        else:
            super().keyPressEvent(event)

    def save_layout(self):
        """Ctrl+Shift+S: make the current size and position the one it always opens at."""
        if self.isFullScreen() or self.isMinimized():
            return
        maximized = self.isMaximized()
        rect = self.normalGeometry() if maximized else self.geometry()
        store = QSettings(str(WINDOW_FILE), QSettings.IniFormat)
        if rect.isValid():
            store.setValue("x", rect.x())
            store.setValue("y", rect.y())
            store.setValue("width", rect.width())
            store.setValue("height", rect.height())
        store.setValue("maximized", maximized)
        self.clock.setText("Layout saved")
        self.clock_hold_until = time.monotonic() + 2

    def restart(self):
        self.close()
        QProcess.startDetached(sys.executable, restart_args())

    def closeEvent(self, event):
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} closed normally", file=sys.stderr)
        self.timer.stop()
        self.frame_timer.stop()
        self.hotkeys.unregister_all()
        self.logger.close()
        self.sampler.close()
        self.tray.hide()
        self.wifi_poller.stop()
        for recorder in (self.tracker, self.netwatch):
            if recorder:
                recorder.stop()
                recorder.join(timeout=3)  # let it save the last minute
        for window in (self.mini,):
            if window:
                window.close()
        super().closeEvent(event)
        QApplication.quit()


def saved_window(app) -> tuple[QRect, bool] | None:
    """The saved layout, if there is one and it's still on a connected monitor."""
    store = QSettings(str(WINDOW_FILE), QSettings.IniFormat)
    if not store.contains("width"):
        return None
    rect = QRect(int(store.value("x")), int(store.value("y")),
                 int(store.value("width")), int(store.value("height")))
    if not any(sc.availableGeometry().intersects(rect) for sc in app.screens()):
        return None
    return rect, store.value("maximized", False, type=bool)


def already_running(wait_ms: int) -> bool:
    """Only one monitor at a time. After a restart, wait for the old one to finish closing."""
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    # PCMONITOR_INSTANCE lets a test copy run next to the real one (testing builds only)
    name = "PCMonitor.SingleInstance" + os.environ.get("PCMONITOR_INSTANCE", "")
    handle = kernel32.CreateMutexW(None, False, name)
    result = kernel32.WaitForSingleObject(ctypes.c_void_p(handle), wait_ms)
    return result not in (0x0, 0x80)  # WAIT_OBJECT_0 or WAIT_ABANDONED = we own it


def restart_args() -> list[str]:
    """Arguments to start PC Monitor again (the packaged .exe needs no script path)."""
    return ["--restart"] if FROZEN else [str(APP_DIR / "app.py"), "--restart"]


def enable_crash_log():
    """Write any error, and any hard crash, to crash.log in this folder (it's run without a
    console, so otherwise errors would be invisible)."""
    log = open(CRASH_LOG, "a", encoding="utf-8", buffering=1)
    faulthandler.enable(log)
    sys.stderr = log  # so normal-close notes and any other error output land here too
    log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} started\n")

    def log_error(kind, value, tb):
        log.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        log.write("".join(traceback.format_exception(kind, value, tb)))
    sys.excepthook = log_error
    threading.excepthook = lambda args: log_error(args.exc_type, args.exc_value, args.exc_traceback)


def main():
    if already_running(8000 if "--restart" in sys.argv else 0):
        return
    enable_crash_log()
    # Its own taskbar identity (otherwise Windows groups it with Python and shows Python's icon)
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PCMonitor.App")
    app = QApplication(sys.argv)
    app.setApplicationName("PC Monitor")
    app.setWindowIcon(QIcon(str(RESOURCE_DIR / "assets" / "icon.ico")))
    settings = Settings.load()
    T.use(settings.theme)
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(stylesheet())
    pg.setConfigOptions(antialias=True)

    window = Dashboard(settings)
    saved = saved_window(app)
    others = [sc for sc in app.screens() if sc is not app.primaryScreen()]
    if saved:
        rect, maximized = saved
        window.setGeometry(rect)
        window.showMaximized() if maximized else window.show()
    elif OPEN_ON_SECONDARY and others:
        window.setGeometry(others[0].availableGeometry())
        window.showMaximized()
    else:
        window.resize(1600, 950)
        window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
