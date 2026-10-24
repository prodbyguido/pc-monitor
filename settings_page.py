"""The Settings screen. Every change saves immediately and applies right away."""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget)

import startup
import updater
from config import DEV_COPY, LOG_DIR
from theme import THEMES
from version import VERSION

TIME_RANGES = {"2 minutes": 120, "10 minutes": 600, "1 hour": 3600}


class SettingsPage(QWidget):
    def __init__(self, dash):
        super().__init__()
        self.dash = dash
        self.s = dash.settings
        self.checks: dict[str, QCheckBox] = {}

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget(objectName="pagebody")
        scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        page = QVBoxLayout(body)
        page.setContentsMargins(0, 0, 6, 0)
        columns = QHBoxLayout()
        columns.setSpacing(14)
        left, right = QVBoxLayout(), QVBoxLayout()
        for column in (left, right):
            column.setSpacing(6)
            columns.addLayout(column, stretch=1)
        page.addLayout(columns)
        col = left

        g = self.group(col, "Gaming")
        self.check(g, "fps_card", "FPS counter card (displayed and rendered)",
                   "Uses HWiNFO's built-in PresentMon: tick Framerate (Presented) and Framerate "
                   "(Displayed) in HWiNFO → Configure Sensors → HWiNFO Gadget.")
        self.check(g, "session_summary", "Game recap after you stop playing",
                   "Shows how your PC handled the game, what held it back and how to fix it. "
                   "Every recap is saved in Reports → Games.")
        self.check(g, "peak_markers", "Peak markers (highest value since the monitor opened)")

        g = self.group(col, "Alerts")
        self.check(g, "alerts", "Hot warnings when a temperature or memory stays in the red")
        row = QHBoxLayout()
        row.addSpacing(28)
        row.addWidget(QLabel("Warn after"))
        self.seconds = QSpinBox()
        self.seconds.setRange(1, 60)
        self.seconds.setValue(self.s.alert_seconds)
        self.seconds.valueChanged.connect(lambda v: self.set("alert_seconds", v))
        row.addWidget(self.seconds)
        row.addWidget(QLabel("seconds in the red"))
        row.addStretch()
        g.addLayout(row)
        self.check(g, "alert_pulse", "Pulse the card's border red", indent=True)
        self.check(g, "alert_notification", "Windows notification (needs the tray icon)", indent=True)
        self.check(g, "alert_sound", "Play a warning sound", indent=True)
        self.check(g, "throttle_badges", "Throttle badges when the CPU or GPU slows itself down")

        g = self.group(col, "Looks")
        self.check(g, "smooth_animation", "Smooth animation (uses slightly more CPU)")
        self.check(g, "tray_icon", "Tray icon with live CPU temp")
        row = QHBoxLayout()
        row.addWidget(QLabel("Theme"))
        theme = QComboBox()
        theme.addItems(list(THEMES))
        theme.setCurrentText(self.s.theme)
        theme.currentTextChanged.connect(lambda v: self.set("theme", v))
        row.addWidget(theme)
        row.addWidget(QLabel("(the monitor restarts to apply)", objectName="note"))
        row.addStretch()
        g.addLayout(row)

        left.addStretch()
        col = right
        g = self.group(col, "Overlay && mini mode")
        self.check(g, "overlay", "Overlay mode: always on top and see-through")
        row = QHBoxLayout()
        row.addSpacing(28)
        row.addWidget(QLabel("See-through"))
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(30, 100)
        self.opacity.setValue(self.s.overlay_opacity)
        self.opacity_label = QLabel(f"{self.s.overlay_opacity}% visible")
        self.opacity.valueChanged.connect(self._opacity_changed)
        row.addWidget(self.opacity, stretch=1)
        row.addWidget(self.opacity_label)
        g.addLayout(row)
        self.check(g, "overlay_click_through", "Clicks pass through to the window underneath",
                   "While this is on you can't click the monitor itself. Press Ctrl+Alt+O, or use "
                   "the tray icon, to turn overlay mode off again.", indent=True)
        self.check(g, "hotkeys", "Hotkeys: Ctrl+Alt+O overlay on/off, Ctrl+Alt+M mini mode on/off")
        self.hotkey_note = QLabel("", objectName="note")
        self.hotkey_note.setWordWrap(True)
        g.addWidget(self.hotkey_note)
        mini = QPushButton("Switch to mini mode now")
        mini.clicked.connect(lambda: self.dash.toggle_mini())
        g.addWidget(mini, alignment=Qt.AlignLeft)

        g = self.group(col, "History")
        row = QHBoxLayout()
        row.addWidget(QLabel("Graphs show the last"))
        self.range = QComboBox()
        self.range.addItems(list(TIME_RANGES))
        self.range.currentTextChanged.connect(lambda v: self.set("time_range", TIME_RANGES[v]))
        row.addWidget(self.range)
        row.addStretch()
        g.addLayout(row)
        self.check(g, "logging", "Save readings to a log file",
                   "Off by default, so nothing is saved. When on, one CSV file per day goes in "
                   "the logs folder; open it with Excel or Google Sheets.")
        logs = QPushButton("Open logs folder")
        logs.clicked.connect(self._open_logs)
        g.addWidget(logs, alignment=Qt.AlignLeft)

        g = self.group(col, "Tracking && reports")
        self.check(g, "app_tracker", "Track which apps use the most CPU and memory",
                   "Every 5 seconds, records each app's name and its CPU and memory use. Saved only "
                   "on this PC and kept for 90 days. No window titles, websites or anything you do "
                   "inside apps.")
        self.check(g, "net_watcher", "Network watcher: ping, Wi-Fi signal and internet drops",
                   "Pings your router and Cloudflare (1.1.1.1) every 2 seconds, a few KB per "
                   "minute, and shows live ping on the Network card. Speed tests only run when you "
                   "press the button in Reports.")
        reports = QPushButton("Open reports")
        reports.clicked.connect(lambda: self.dash.open_reports())
        g.addWidget(reports, alignment=Qt.AlignLeft)

        g = self.group(col, "About your PC")
        row = QHBoxLayout()
        row.addWidget(QLabel("Internet plan speed"))
        self.plan = QSpinBox()
        self.plan.setRange(0, 10000)
        self.plan.setSingleStep(50)
        self.plan.setSuffix(" Mbps")
        self.plan.setSpecialValueText("Not set")
        self.plan.setValue(self.s.internet_plan_mbps)
        self.plan.valueChanged.connect(lambda v: self.set("internet_plan_mbps", v))
        row.addWidget(self.plan)
        row.addStretch()
        g.addLayout(row)
        g.addWidget(self._note("Used by the Wi-Fi score: Wi-Fi faster than your plan brings no extra "
                               "benefit."))
        g.addWidget(QLabel("Your own apps"))
        self.my_apps = QLineEdit(placeholderText="e.g. MyApp.exe, MyTool.exe")
        self.my_apps.setText(self.s.my_apps)
        self.my_apps.editingFinished.connect(lambda: self.set("my_apps", self.my_apps.text().strip()))
        g.addWidget(self.my_apps)
        g.addWidget(self._note("Apps you're building yourself: never counted as games, and always get "
                               "a full timeline in Reports → Apps. Separate several with commas."))
        g.addWidget(QLabel("Extra PC details for game reports"))
        self.notes = QLineEdit(placeholderText="e.g. motherboard, cooler, RAM speed, monitors")
        self.notes.setText(self.s.my_pc_notes)
        self.notes.editingFinished.connect(lambda: self.set("my_pc_notes", self.notes.text().strip()))
        g.addWidget(self.notes)
        g.addWidget(self._note("Added to \"Copy for Claude\" reports. CPU, GPU and RAM are detected "
                               "automatically."))

        right.addStretch()
        col = left
        left.takeAt(left.count() - 1)  # put these above the left column's spacer
        g = self.group(col, "Startup, updates && help")
        self.autostart = QCheckBox("Start PC Monitor when Windows starts")
        self.autostart.toggled.connect(self._set_autostart)
        g.addWidget(self.autostart)
        row = QHBoxLayout()
        guide = QPushButton("Setup guide")
        guide.clicked.connect(lambda: self.dash.show_page("setup"))
        row.addWidget(guide)
        row.addWidget(self._note("Checks HWiNFO is set up and shows how to fix anything missing."))
        row.addStretch()
        g.addLayout(row)
        g.addWidget(QLabel(f"PC Monitor version {VERSION}", objectName="subname"))
        if DEV_COPY:
            g.addWidget(self._note("This is the developer copy: it never checks for or installs "
                                   "updates. Changes made here are what gets published."))
        elif updater.can_check():
            self.check(g, "update_check", "Check for updates when PC Monitor starts")
            row = QHBoxLayout()
            check_now = QPushButton("Check for updates")
            check_now.clicked.connect(lambda: self.dash.check_for_updates(manual=True))
            row.addWidget(check_now)
            self.update_button = QPushButton("Update now")
            self.update_button.clicked.connect(lambda: self.dash.install_update())
            self.update_button.hide()
            row.addWidget(self.update_button)
            row.addStretch()
            g.addLayout(row)
        self.update_status = self._note("")
        g.addWidget(self.update_status)
        row = QHBoxLayout()
        reset = QPushButton("Reset peaks")
        reset.clicked.connect(lambda: self.dash.reset_peaks())
        self.summary = QPushButton("Last game recap")
        self.summary.clicked.connect(lambda: self.dash.show_last_summary())
        row.addWidget(reset)
        row.addWidget(self.summary)
        row.addStretch()
        g.addLayout(row)
        left.addStretch()
        self.sync()

    def _note(self, text: str) -> QLabel:
        label = QLabel(text, objectName="note")
        label.setWordWrap(True)
        return label

    def _set_autostart(self, on: bool):
        startup.set_enabled(on)
        self.sync()

    def show_update_status(self, text: str, can_install: bool = False):
        self.update_status.setText(text)
        if hasattr(self, "update_button"):
            self.update_button.setVisible(can_install)

    def showEvent(self, event):
        super().showEvent(event)
        self.sync()

    def group(self, parent: QVBoxLayout, title: str) -> QVBoxLayout:
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        layout.setSpacing(8)
        parent.addWidget(box)
        return layout

    def check(self, layout: QVBoxLayout, field: str, text: str, note: str = "", indent: bool = False):
        box = QCheckBox(text)
        box.toggled.connect(lambda on: self.set(field, on))
        self.checks[field] = box
        row = QHBoxLayout()
        if indent:
            row.addSpacing(28)
        row.addWidget(box)
        row.addStretch()
        layout.addLayout(row)
        if note:
            label = QLabel(note, objectName="note")
            label.setWordWrap(True)
            label.setContentsMargins(56 if indent else 28, 0, 0, 4)
            layout.addWidget(label)

    def set(self, field: str, value):
        if getattr(self.s, field) == value:
            return
        setattr(self.s, field, value)
        self.s.save()
        self._refresh_enabled()
        self.dash.apply_settings(field)

    def _opacity_changed(self, value: int):
        self.opacity_label.setText(f"{value}% visible")
        self.set("overlay_opacity", value)

    def _open_logs(self):
        LOG_DIR.mkdir(exist_ok=True)
        os.startfile(LOG_DIR)

    def _refresh_enabled(self):
        s = self.s
        for field in ("alert_pulse", "alert_notification", "alert_sound"):
            self.checks[field].setEnabled(s.alerts)
        self.seconds.setEnabled(s.alerts)
        self.checks["alert_notification"].setEnabled(s.alerts and s.tray_icon)
        self.opacity.setEnabled(s.overlay)
        self.checks["overlay_click_through"].setEnabled(s.overlay)
        failed = self.dash.hotkeys.failed
        self.hotkey_note.setText(f"Another app is already using: {', '.join(failed)}" if failed else "")
        self.hotkey_note.setVisible(bool(failed))
        self.summary.setEnabled(self.dash.has_recap)

    def sync(self):
        """Refresh the controls from the settings (e.g. after a hotkey changed one)."""
        for field, box in self.checks.items():
            box.blockSignals(True)
            box.setChecked(getattr(self.s, field))
            box.blockSignals(False)
        name = next((n for n, sec in TIME_RANGES.items() if sec == self.s.time_range), "2 minutes")
        self.range.blockSignals(True)
        self.range.setCurrentText(name)
        self.range.blockSignals(False)
        self.autostart.blockSignals(True)
        self.autostart.setChecked(startup.enabled())
        self.autostart.blockSignals(False)
        self._refresh_enabled()
