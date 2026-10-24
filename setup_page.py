"""The setup guide: checks what PC Monitor needs and explains how to fix anything missing.

Shown the first time PC Monitor runs, and any time from Settings → Setup guide.
"""
import winreg

import psutil
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

import hwinfo_setup
import startup
from sensors import cpu_temp_reading, gpu_from_hwinfo, read_hwinfo
from status import COOL, GOOD, WARM
from theme import T

HWINFO_URL = "https://www.hwinfo.com/download/"
# The "Set up HWiNFO for me" button (hwinfo_setup.py) stays off until it's been tested for real
# on a PC without HWiNFO; until then the guide shows the manual steps.
AUTO_SETUP = False


class Step(QFrame):
    """One item on the checklist: a status mark, a title, and how to fix it."""

    def __init__(self, title: str, how: str, optional: bool = False):
        super().__init__(objectName="step")
        self.optional = optional
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(14)
        self.mark = QLabel("", objectName="stepmark")
        self.mark.setFixedWidth(34)
        self.mark.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        layout.addWidget(self.mark)
        text = QVBoxLayout()
        text.setSpacing(4)
        self.title = QLabel(title, objectName="findingtitle")
        self.title.setWordWrap(True)
        text.addWidget(self.title)
        self.how = QLabel(how)
        self.how.setWordWrap(True)
        self.how.setTextFormat(Qt.RichText)
        self.how.setOpenExternalLinks(True)
        text.addWidget(self.how)
        layout.addLayout(text, stretch=1)

    def set_done(self, done: bool | None, note: str = ""):
        """done: True = ✓, False = needs doing, None = can't tell yet."""
        if done:
            symbol, color = "✓", GOOD
        elif done is None:
            symbol, color = "…", COOL
        else:
            symbol, color = ("○" if self.optional else "!"), (COOL if self.optional else WARM)
        self.mark.setText(symbol)
        self.mark.setStyleSheet(f"color: {color}; font-size: 20pt; font-weight: 800;")
        self.title.setStyleSheet(f"color: {color};")
        self.how.setVisible(not done)
        self.setStyleSheet(f"QFrame#step {{ background: {T.PLOT_BG}; border: 1px solid {T.BORDER};"
                           f" border-left: 5px solid {color}; border-radius: 8px; }}")
        if note:
            self.title.setToolTip(note)


class SetupPage(QWidget):
    def __init__(self, dash):
        super().__init__()
        self.dash = dash
        top = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(0)
        heading.addWidget(QLabel("SETUP GUIDE", objectName="title"))
        heading.addWidget(QLabel("Let's get PC Monitor reading everything", objectName="heading"))
        heading.addWidget(QLabel("This checks itself every couple of seconds, so just follow the steps "
                                 "that aren't ticked yet.", objectName="subheading"))
        top.addLayout(heading, stretch=1)
        done = QPushButton("Done  (Esc)")
        done.clicked.connect(self.finish)
        top.addWidget(done, alignment=Qt.AlignTop)

        if AUTO_SETUP:
            how = ("PC Monitor gets CPU temperatures and FPS from the free program HWiNFO. Click the "
                   "button: PC Monitor installs it from its official source (if needed), sets it up to "
                   "run quietly in the tray and share its readings, and starts it. Windows will ask "
                   "for permission once: click <b>Yes</b>.")
        else:
            how = (f"PC Monitor gets CPU temperatures and FPS from the free program HWiNFO.<br>"
                   f"<b>1.</b> Download it from <a href='{HWINFO_URL}' style='color:{COOL}'>hwinfo.com</a> "
                   "(the official site) and install it.<br>"
                   "<b>2.</b> Open HWiNFO. On its small start window click <b>Settings</b> and tick "
                   "<b>Auto Start</b>, <b>Minimize Main Window on Startup</b>, <b>Minimize Sensors on "
                   "Startup</b> and <b>Minimize Sensors instead of closing</b>, then <b>OK</b>. That "
                   "keeps it running quietly in the tray, starting with Windows.<br>"
                   "<b>3.</b> Tick <b>Sensors-only</b> and click <b>Start</b>.<br>"
                   "<b>4.</b> In the sensor window, click the <b>gear icon (Configure Sensors)</b> at the "
                   "bottom, open the <b>HWiNFO Gadget</b> tab and tick <b>Enable reporting to "
                   "Gadget</b>. Keep that tab open for the next step.")
        self.hwinfo = Step("HWiNFO is installed, running and sharing", how)
        self.hwinfo_autostart = QCheckBox("Also start HWiNFO when Windows starts (recommended)")
        self.hwinfo_autostart.setChecked(True)
        self.hwinfo_button = QPushButton("Set up HWiNFO for me")
        self.hwinfo_button.clicked.connect(self._setup_hwinfo)
        self.hwinfo_status = QLabel("", objectName="note")
        self.hwinfo_status.setWordWrap(True)
        manual = QLabel(f"Prefer to do it yourself? Download HWiNFO from "
                        f"<a href='{HWINFO_URL}' style='color:{COOL}'>hwinfo.com</a>, install it, "
                        "open it with <b>Sensors-only</b> ticked, then in its sensor window click the "
                        "<b>gear icon</b> → <b>HWiNFO Gadget</b> tab → tick <b>Enable reporting to "
                        "Gadget</b>.", objectName="note")
        manual.setWordWrap(True)
        manual.setOpenExternalLinks(True)
        box = self.hwinfo.layout().itemAt(1).layout()
        row = QHBoxLayout()
        row.addWidget(self.hwinfo_button)
        row.addWidget(self.hwinfo_autostart)
        row.addStretch()
        box.addLayout(row)
        box.addWidget(self.hwinfo_status)
        box.addWidget(manual)
        self.hwinfo_extras = [self.hwinfo_button, self.hwinfo_autostart, manual] if AUTO_SETUP else []
        for w in (self.hwinfo_button, self.hwinfo_autostart, manual):
            w.setVisible(AUTO_SETUP)
        self.setup_status_file = None

        self.cpu_temp = Step(
            "CPU temperature is shared",
            "Choose what HWiNFO shares. In HWiNFO's <b>HWiNFO Gadget</b> tab (sensor window → "
            "<b>gear icon</b>), find your CPU's main temperature in the list: <b>CPU (Tctl/Tdie)</b> "
            "on AMD Ryzen, or <b>CPU Package</b> on Intel. Click it, tick <b>Report value in "
            "Gadget</b>, then <b>OK</b>. Nice extras to tick the same way: <b>CPU Package Power</b> "
            "and your SSD's <b>Drive Temperature</b>; each gets its own card.")
        self.gpu = Step(
            "Graphics card readings",
            "Your graphics card isn't NVIDIA, so its readings come from HWiNFO too. In the "
            "<b>HWiNFO Gadget</b> tab, under your GPU, tick <b>GPU Temperature</b>, "
            "<b>GPU Utilization</b> (or GPU Core Load), <b>GPU Memory Usage</b>, "
            "<b>GPU D3D Memory Dedicated</b> and the GPU's power reading (for example <b>Total "
            "Board Power</b> or <b>GPU ASIC Power</b>).")
        self.fps = Step(
            "Optional: FPS counter",
            "Start a game. While it's running, HWiNFO shows a <b>PresentMon</b> section: tick "
            "<b>Framerate (Presented)</b> and <b>Framerate (Displayed)</b> in the Gadget tab. For "
            "the best game recaps also tick <b>Frame Time</b>, <b>GPU Busy</b> and <b>CPU Busy</b>.",
            optional=True)
        self.autostart = Step(
            "Optional: PC Monitor starts with Windows",
            "Turn it on here (or later in Settings → Startup & updates).", optional=True)
        autostart_button = QPushButton("Start PC Monitor with Windows")
        autostart_button.clicked.connect(self._enable_autostart)
        self.autostart.layout().itemAt(1).layout().addWidget(autostart_button, alignment=Qt.AlignLeft)

        holder = QWidget(objectName="pagebody")
        steps = QVBoxLayout(holder)
        steps.setContentsMargins(0, 0, 8, 0)
        steps.setSpacing(8)
        for step in (self.hwinfo, self.cpu_temp, self.gpu, self.fps, self.autostart):
            steps.addWidget(step)
        steps.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addLayout(top)
        layout.addWidget(scroll, stretch=1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check)

    def showEvent(self, event):
        super().showEvent(event)
        self.check()
        self.timer.start(2000)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def check(self):
        running = any((p.info["name"] or "").lower().startswith("hwinfo")
                      for p in psutil.process_iter(["name"]))
        readings = read_hwinfo() if running else None
        ready = running and (sharing_enabled() or bool(readings))
        self.hwinfo.set_done(ready)
        for w in self.hwinfo_extras:  # the button and help only matter until it's done
            w.setVisible(not ready)
        if self.setup_status_file:
            status = hwinfo_setup.read_status(self.setup_status_file)
            self.hwinfo_status.setText(status["message"])
            if status["step"] in ("done", "failed"):
                self.setup_status_file = None
                self.hwinfo_button.setEnabled(True)
                if status["step"] == "failed":
                    self.hwinfo_status.setText(f"That didn't work: {status['message']} You can try "
                                               "again, or follow the manual steps below.")
        self.hwinfo_status.setVisible(bool(self.hwinfo_status.text()) and not ready)
        self.cpu_temp.set_done(cpu_temp_reading(readings) is not None)
        gpu = self.dash.sampler.gpu
        nvidia = gpu.handle is not None
        self.gpu.setVisible(not nvidia)
        if not nvidia:
            self.gpu.set_done(gpu_from_hwinfo(readings) is not None)
        seen_fps = any("framerate" in r.label.lower() for r in readings or [])
        self.fps.set_done(True if seen_fps else False)
        self.autostart.set_done(startup.enabled())

    def _enable_autostart(self):
        startup.set_enabled(True)
        self.check()

    def _setup_hwinfo(self):
        if not hwinfo_setup.install_dir() and not hwinfo_setup.can_install():
            self.hwinfo_status.setText("Windows' app installer (winget) isn't available on this PC, "
                                       "so please use the manual steps below.")
            self.hwinfo_status.show()
            return
        self.hwinfo_button.setEnabled(False)
        self.setup_status_file = hwinfo_setup.start_setup(self.hwinfo_autostart.isChecked())
        if self.setup_status_file is None:  # the Windows prompt was declined
            self.hwinfo_button.setEnabled(True)
            self.hwinfo_status.setText("Windows' permission prompt was declined, so nothing was "
                                       "changed. Click the button again when you're ready.")
        self.check()

    def finish(self):
        st = self.dash.settings
        if not st.first_run_done:
            st.first_run_done = True
            st.save()
        self.dash.show_page("dashboard")


def sharing_enabled() -> bool:
    """Is HWiNFO's "Enable reporting to Gadget" switched on?"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\HWiNFO64\Sensors") as key:
            return winreg.QueryValueEx(key, "EnableVSB")[0] == 1
    except OSError:
        return False
