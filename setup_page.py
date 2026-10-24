"""The setup guide: checks what PC Monitor needs and explains how to fix anything missing.

Shown the first time PC Monitor runs, and any time from Settings → Setup guide.
"""
import psutil
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

import startup
from sensors import cpu_temp_reading, gpu_from_hwinfo, read_hwinfo
from status import COOL, GOOD, WARM
from theme import T

HWINFO_URL = "https://www.hwinfo.com/download/"


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

        self.hwinfo = Step(
            "HWiNFO is running",
            f"PC Monitor gets CPU temperatures (and a few other readings) from the free program "
            f"HWiNFO. Download it from <a href='{HWINFO_URL}' style='color:{COOL}'>hwinfo.com</a> "
            "(the official site), install it, open it, tick <b>Sensors-only</b> and click "
            "<b>Start</b>.")
        self.gadget = Step(
            "HWiNFO is sharing its readings",
            "In HWiNFO's sensor window, click the <b>gear icon (Configure Sensors)</b> at the "
            "bottom, open the <b>HWiNFO Gadget</b> tab, tick <b>Enable reporting to Gadget</b> "
            "and click <b>OK</b>.")
        self.cpu_temp = Step(
            "CPU temperature is shared",
            "In that same <b>HWiNFO Gadget</b> tab, find your CPU's main temperature: "
            "<b>CPU (Tctl/Tdie)</b> on AMD Ryzen, or <b>CPU Package</b> on Intel. Click it and tick "
            "<b>Report value in Gadget</b>. Nice extras to tick the same way: <b>CPU Package "
            "Power</b> and your SSD's <b>Drive Temperature</b>. Each one gets its own card.")
        self.gpu = Step(
            "Graphics card readings",
            "Your graphics card isn't NVIDIA, so its readings come from HWiNFO too. In the "
            "<b>HWiNFO Gadget</b> tab, under your GPU, tick <b>GPU Temperature</b>, "
            "<b>GPU Utilization</b> (or GPU Core Load), <b>GPU Memory Usage</b>, "
            "<b>GPU D3D Memory Dedicated</b> and the GPU's power reading (for example <b>Total "
            "Board Power</b> or <b>GPU ASIC Power</b>).")
        self.hwinfo_start = Step(
            "Optional: HWiNFO starts with Windows",
            "So you never have to open it yourself: close HWiNFO, open it again, click "
            "<b>Settings</b> on its small start window and tick <b>Auto Start</b>, <b>Minimize "
            "Sensors on Startup</b>, <b>Minimize Main Window on Startup</b> and <b>Minimize "
            "Sensors instead of closing</b>. Then click <b>OK</b>.", optional=True)
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
        for step in (self.hwinfo, self.gadget, self.cpu_temp, self.gpu, self.hwinfo_start,
                     self.fps, self.autostart):
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
        self.hwinfo.set_done(running)
        self.gadget.set_done(bool(readings) if running else False)
        self.cpu_temp.set_done(cpu_temp_reading(readings) is not None)
        gpu = self.dash.sampler.gpu
        nvidia = gpu.handle is not None
        self.gpu.setVisible(not nvidia)
        if not nvidia:
            self.gpu.set_done(gpu_from_hwinfo(readings) is not None)
        self.hwinfo_start.set_done(False)  # can't be checked from here, so always shown as a tip
        seen_fps = any("framerate" in r.label.lower() for r in readings or [])
        self.fps.set_done(True if seen_fps else False)
        self.autostart.set_done(startup.enabled())

    def _enable_autostart(self):
        startup.set_enabled(True)
        self.check()

    def finish(self):
        st = self.dash.settings
        if not st.first_run_done:
            st.first_run_done = True
            st.save()
        self.dash.show_page("dashboard")
