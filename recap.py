"""The game recap screen: how your PC handled a game, what held it back, and how to fix it."""
import math
import os
import subprocess
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QFrame, QGroupBox, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)
import pyqtgraph as pg

from analysis import BAD, GOOD as SEV_GOOD, INFO, WARN, analyze
from export import report_text, save_csv
from reports import Tile, time_plot
from session import GameSession, fmt_duration
from status import COOL, CPU_TEMP, GOOD, GPU_TEMP, HOT, WARM, fps_status, temp_status
from theme import T

SEVERITY_COLORS = {SEV_GOOD: GOOD, INFO: COOL, WARN: WARM, BAD: HOT}


SMOOTH_SECONDS = 5  # charts show 5-second averages so trends are readable; drops are dots


def spans(times: list[float], gap: float = 2) -> list[tuple[float, float]]:
    """Group sorted times into (start, end) stretches, for shading on the chart."""
    out = []
    for t in times:
        if out and t - out[-1][1] <= gap:
            out[-1][1] = t
        else:
            out.append([t, t])
    return [(lo - 0.5, hi + 0.5) for lo, hi in out]


def series(samples, key, start=None):
    """Time and 5-second-average values for one sample key (gaps where there's no data)."""
    xs, ys, bucket, bucket_t = [], [], [], None
    for s in samples:
        v = s.get(key)
        b = int(s["t"] // SMOOTH_SECONDS)
        if bucket_t is not None and b != bucket_t:
            xs.append(bucket_t * SMOOTH_SECONDS + SMOOTH_SECONDS / 2)
            ys.append(sum(bucket) / len(bucket) if bucket else math.nan)
            bucket = []
        bucket_t = b
        if v is not None:
            bucket.append(v)
    if bucket_t is not None:
        xs.append(bucket_t * SMOOTH_SECONDS + SMOOTH_SECONDS / 2)
        ys.append(sum(bucket) / len(bucket) if bucket else math.nan)
    return xs, ys


class FindingCard(QFrame):
    def __init__(self, finding):
        super().__init__()
        color = SEVERITY_COLORS[finding.severity]
        self.setObjectName("finding")
        self.setStyleSheet(f"QFrame#finding {{ background: {T.PLOT_BG}; border: 1px solid {T.BORDER};"
                           f" border-left: 5px solid {color}; border-radius: 8px; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 12, 10)
        layout.setSpacing(4)
        title = QLabel(finding.title, objectName="findingtitle")
        title.setStyleSheet(f"color: {color};")
        title.setWordWrap(True)
        layout.addWidget(title)
        detail = QLabel(finding.detail)
        detail.setWordWrap(True)
        layout.addWidget(detail)
        if finding.fix:
            fix = QLabel(f"<b>How to fix:</b> {finding.fix}", objectName="fix")
            fix.setWordWrap(True)
            layout.addWidget(fix)


class RecapPage(QWidget):
    def __init__(self, dash):
        super().__init__()
        self.dash = dash

        top = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(0)
        heading.addWidget(QLabel("GAME RECAP", objectName="title"))
        self.name = QLabel("", objectName="heading")
        heading.addWidget(self.name)
        self.when = QLabel("", objectName="subheading")
        heading.addWidget(self.when)
        top.addLayout(heading)
        top.addSpacing(24)
        self.verdict = QLabel("", objectName="verdict")
        self.verdict.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        top.addWidget(self.verdict, stretch=1, alignment=Qt.AlignVCenter)
        top.addSpacing(16)
        buttons = QVBoxLayout()
        buttons.setSpacing(6)
        row = QHBoxLayout()
        copy = QPushButton("Copy for Claude")
        copy.setToolTip("Copies a detailed text report of this session, ready to paste into a chat")
        copy.clicked.connect(self._copy_report)
        row.addWidget(copy)
        save = QPushButton("Save data (CSV)")
        save.setToolTip("Saves every second of this session as a spreadsheet file in the exports folder")
        save.clicked.connect(self._save_csv)
        row.addWidget(save)
        close = QPushButton("Close  (Esc)")
        close.clicked.connect(lambda: self.dash.close_recap())
        row.addWidget(close)
        buttons.addLayout(row)
        self.export_note = QLabel("", objectName="note")
        self.export_note.setAlignment(Qt.AlignRight)
        buttons.addWidget(self.export_note)
        top.addLayout(buttons)
        self.session = None
        self.result = None

        tiles = QHBoxLayout()
        self.t_fps = Tile("Average FPS")
        self.t_low = Tile("1% low")
        self.t_drops = Tile("Big drops")
        self.t_limit = Tile("Limited by")
        self.t_cpu = Tile("Peak CPU temp")
        self.t_gpu = Tile("Peak GPU temp")
        for tile in (self.t_fps, self.t_low, self.t_drops, self.t_limit, self.t_cpu, self.t_gpu):
            tiles.addWidget(tile)

        charts = QVBoxLayout()
        box = QGroupBox("Frame rate  (red dots = big drops, grey = tabbed out / menus)")
        v = QVBoxLayout(box)
        self.fps_plot = time_plot()
        self.fps_plot.addLegend(offset=(10, 4), labelTextColor=T.TEXT, colCount=2)
        v.addWidget(self.fps_plot)
        charts.addWidget(box, stretch=1)
        box = QGroupBox("CPU and GPU load")
        v = QVBoxLayout(box)
        self.load_plot = time_plot()
        self.load_plot.addLegend(offset=(10, 4), labelTextColor=T.TEXT, colCount=3)
        self.load_plot.setXLink(self.fps_plot)
        v.addWidget(self.load_plot)
        charts.addWidget(box, stretch=1)

        box = QGroupBox("What happened, and how to fix it")
        v = QVBoxLayout(box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        holder = QWidget(objectName="findings")
        self.findings = QVBoxLayout(holder)
        self.findings.setContentsMargins(0, 0, 6, 0)
        self.findings.setSpacing(8)
        scroll.setWidget(holder)
        v.addWidget(scroll)

        main = QHBoxLayout()
        main.setSpacing(12)
        main.addLayout(charts, stretch=11)
        main.addWidget(box, stretch=10)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addLayout(top)
        layout.addLayout(tiles)
        layout.addLayout(main, stretch=1)

    def _copy_report(self):
        if self.session:
            st = self.dash.settings
            QApplication.clipboard().setText(report_text(self.session, self.result, st.my_pc_notes,
                                                         st.internet_plan_mbps))
            self.export_note.setText("Copied: paste it into a chat with Ctrl+V")

    def _save_csv(self):
        if self.session:
            path = save_csv(self.session)
            subprocess.Popen(["explorer", "/select,", path])  # show the file in File Explorer
            self.export_note.setText(f"Saved to exports\\{os.path.basename(path)}")

    def show_session(self, session: GameSession):
        if not session.meta.get("cpu_name"):  # sessions saved before this was recorded
            session.meta = {**self.dash.pc_facts(session.meta.get("ram_total_gb", 31.1)),
                            **session.meta}
        result = analyze(session.samples, session.meta)
        self.session, self.result = session, result
        self.export_note.setText("")
        st = result.stats
        self.name.setText(session.name)
        self.when.setText(f"{time.strftime('%a %d %b, %H:%M', time.localtime(session.start))} – "
                          f"{time.strftime('%H:%M', time.localtime(session.last_active))}   ·   "
                          f"played {fmt_duration(session.duration)}")
        color = SEVERITY_COLORS[result.severity]
        self.verdict.setText(result.verdict)
        self.verdict.setStyleSheet(f"background: {color}; color: #0b0d12;")

        feel = st.get("avg_rfps")
        if feel:
            fg = st.get("frame_gen")
            self.t_fps.set(f"{st['avg_fps']:.0f}",
                           f"{feel:.0f} rendered ({fg}× frame gen)" if fg else "rendered by the game",
                           fps_status(feel))
            self.t_low.set(f"{st['low1']:.0f}", "worst 1% of seconds", fps_status(st["low1"]))
            drops = st.get("dips", 0)
            self.t_drops.set(str(drops), "sudden FPS falls", HOT if drops >= 5 else WARM if drops else GOOD)
        else:
            for tile in (self.t_fps, self.t_low, self.t_drops):
                tile.set("—", "no frame data")
        what, why, severity = result.limiter
        self.t_limit.set(what, why, SEVERITY_COLORS[severity])
        self.t_limit.setToolTip(why)
        ct, gt = st.get("peak_cpu_temp"), st.get("peak_gpu_temp")
        self.t_cpu.set(f"{ct:.0f} °C" if ct else "—", "", temp_status(ct, CPU_TEMP, 100) if ct else None)
        self.t_gpu.set(f"{gt:.0f} °C" if gt else "—", "", temp_status(gt, GPU_TEMP, 100) if gt else None)

        samples = session.samples
        self.fps_plot.clear()
        lines = ((("fps", "Displayed", "#4f9dff"), ("rfps", "Rendered", "#b38bff"))
                 if st.get("frame_gen") else (("rfps", "FPS", "#4f9dff"),))
        top = 0
        for key, name, c in lines:
            if any(s.get(key) for s in samples):
                xs, ys = series(samples, key)
                self.fps_plot.plot(xs, ys, name=name, pen=pg.mkPen(c, width=2), connect="finite")
                top = max([top] + [y for y in ys if not math.isnan(y)])
        if result.dips:
            by_t = {s["t"]: s.get("rfps") or s.get("fps") for s in samples}
            self.fps_plot.plot(result.dips, [by_t.get(t) or 0 for t in result.dips], pen=None,
                               symbol="o", symbolSize=9, symbolBrush=HOT, symbolPen=None)
        self.fps_plot.setYRange(0, max(top, 30) * 1.3, padding=0)  # headroom for the legend
        self.load_plot.clear()
        for key, name, c in (("gpu", "GPU", "#3dd68c"), ("core", "Busiest CPU core", "#ff9f43"),
                             ("cpu", "CPU overall", "#f5c542")):
            if any(s.get(key) is not None for s in samples):
                xs, ys = series(samples, key)
                self.load_plot.plot(xs, ys, name=name, pen=pg.mkPen(c, width=2), connect="finite")
        self.load_plot.setYRange(0, 130, padding=0)
        for lo, hi in spans(result.away):  # grey = tabbed out / menus, not counted
            for plot in (self.fps_plot, self.load_plot):
                plot.addItem(pg.LinearRegionItem(values=(lo, hi), movable=False,
                                                 brush=pg.mkBrush(140, 150, 170, 45), pen=pg.mkPen(None)))
        self.load_plot.getPlotItem().getAxis("left").setTicks([[(v, f"{v}%") for v in (0, 25, 50, 75, 100)]])
        if samples:
            self.fps_plot.setXRange(samples[0]["t"], samples[-1]["t"], padding=0.01)

        while self.findings.count():
            widget = self.findings.takeAt(0).widget()
            if widget:
                widget.hide()  # hide now: deleteLater only removes it a moment later
                widget.setParent(None)
                widget.deleteLater()
        for finding in result.findings:
            self.findings.addWidget(FindingCard(finding))
        self.findings.addStretch()
