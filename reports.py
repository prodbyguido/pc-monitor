"""The Reports window: pick a day and see which apps used your PC the most, plus your network."""
import math
import threading
import time
from datetime import datetime, timedelta

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox, QFrame, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
                               QSizePolicy, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)
import pyqtgraph as pg

import netwatch
import tracker
from analysis import BAD, GOOD as SEV_GOOD, INFO, WARN, analyze
from db import connect, day_bounds, days_with_data
from session import load as load_session, sessions_on
from status import COOL, GOOD, HOT, WARM
from theme import T

SEVERITY_COLORS = {SEV_GOOD: GOOD, INFO: COOL, WARN: WARM, BAD: HOT}

LINE_COLORS = ["#4f9dff", "#ff9f43", "#b38bff", "#3dd6d0", "#f5c542"]
LIVE_REFRESH_MS = 5000  # how often "today" refreshes while the window is open


def fmt_bytes(b: float) -> str:
    return f"{b / 1024**3:.1f} GB" if b >= 1024**3 else f"{b / 1024**2:.0f} MB"


def fmt_minutes(minutes: float) -> str:
    h, m = divmod(int(round(minutes)), 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def fmt_seconds(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    return f"{m}m {s:02d}s" if m < 60 else fmt_minutes(seconds / 60)


def clock(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def day_label(day: str) -> str:
    d = datetime.strptime(day, "%Y-%m-%d").date()
    today = datetime.now().date()
    pretty = d.strftime("%a %d %b %Y")
    if d == today:
        return f"Today  ·  {pretty}"
    if d == today - timedelta(days=1):
        return f"Yesterday  ·  {pretty}"
    return pretty


class Tile(QFrame):
    """A small summary box: a title and one big number."""

    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.addWidget(QLabel(title.upper(), objectName="title"))
        self.value = QLabel("—", objectName="tilevalue")
        self.value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.value)
        self.note = QLabel("", objectName="note")
        self.note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.note)

    def set(self, value: str, note: str = "", color: str | None = None):
        self.value.setText(value)
        self.value.setStyleSheet(f"color: {color};" if color else "")
        self.note.setText(note)


def make_table(headers: list[str], tips: dict[int, str] | None = None) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    for col, tip in (tips or {}).items():
        table.horizontalHeaderItem(col).setToolTip(tip)
    table.verticalHeader().hide()
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.NoSelection)
    table.setFocusPolicy(Qt.NoFocus)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.Stretch)
    for col in range(1, len(headers)):
        header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
    return table


def fill_table(table: QTableWidget, rows: list[tuple], empty: str = "Nothing recorded"):
    scroll = table.verticalScrollBar().value()  # keep your place when it refreshes
    _fill_table(table, rows, empty)
    table.verticalScrollBar().setValue(scroll)


def _fill_table(table: QTableWidget, rows: list[tuple], empty: str):
    if not rows:
        table.setRowCount(1)
        item = QTableWidgetItem(empty)
        item.setForeground(QColor(T.MUTED))
        table.setItem(0, 0, item)
        for col in range(1, table.columnCount()):
            table.setItem(0, col, QTableWidgetItem(""))
        return
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            item = QTableWidgetItem(str(value))
            if c > 0:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(r, c, item)


def time_plot(height: int | None = None) -> pg.PlotWidget:
    plot = pg.PlotWidget(background=T.CARD, axisItems={"bottom": pg.DateAxisItem(orientation="bottom")})
    item = plot.getPlotItem()
    item.setMenuEnabled(False)
    item.hideButtons()
    plot.setMouseEnabled(x=True, y=False)  # scroll to zoom in on part of the day
    item.showGrid(x=True, y=True, alpha=T.GRID_ALPHA)
    plot.getViewBox().setBackgroundColor(T.PLOT_BG)
    for name in ("left", "bottom"):
        axis = item.getAxis(name)
        axis.setTextPen(T.MUTED)
        axis.setPen(T.BORDER)
        axis.setStyle(tickFont=QFont("Segoe UI", 9))
    if height:
        plot.setFixedHeight(height)
    return plot


def bucketed(points: list[tuple[float, float]], seconds: int = 600) -> list[tuple[float, float]]:
    """Average into 10-minute steps so a whole day reads as a trend, not noise."""
    buckets: dict[int, list[float]] = {}
    for x, y in points:
        if y is not None:
            buckets.setdefault(int(x // seconds * seconds), []).append(y)
    return [(x + seconds / 2, sum(v) / len(v)) for x, v in sorted(buckets.items())]


def gapped(points: list[tuple[float, float]], gap: float = 150) -> tuple[list, list]:
    """Split a line where no data was recorded, instead of drawing across the gap."""
    xs, ys, last = [], [], None
    for x, y in points:
        if last is not None and x - last > gap:
            xs.append(last + 60)
            ys.append(math.nan)
        xs.append(x)
        ys.append(math.nan if y is None else y)
        last = x
    return xs, ys


class SpeedSignals(QObject):
    progress = Signal(str)
    done = Signal(object)


class ReportsPage(QWidget):
    def __init__(self, dash):
        super().__init__()
        self.dash = dash
        self.con = connect()
        self.app_metric = "cpu"
        self.focus_app = None   # the app chosen in the Apps tables, shown alone on the chart
        self.speed_running = False
        self.shown_day = None    # the day the charts were last drawn for
        self.shown_metric = None
        self.signals = SpeedSignals()
        self.signals.progress.connect(self._speed_progress)
        self.signals.done.connect(self._speed_done)

        top = QHBoxLayout()
        top.addWidget(QLabel("Day"))
        self.day = QComboBox()
        self.day.setMinimumWidth(240)
        self.day.currentIndexChanged.connect(lambda _: self.load())
        top.addWidget(self.day)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        top.addWidget(refresh)
        top.addStretch()
        self.status = QLabel("", objectName="subheading")
        top.addWidget(self.status)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_apps_tab(), "Apps")
        self.tabs.addTab(self._build_network_tab(), "Network")
        self.tabs.addTab(self._build_games_tab(), "Games")
        self.verdicts: dict[int, tuple] = {}  # session id -> (verdict, severity, avg fps), cached

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addLayout(top)
        layout.addWidget(self.tabs, stretch=1)

        self.timer = QTimer(self)  # keep "today" up to date while the window is open
        self.timer.timeout.connect(self._auto_refresh)
        self.timer.start(LIVE_REFRESH_MS)

    # --- building ---------------------------------------------------------------
    def _build_apps_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        tiles = QHBoxLayout()
        self.t_recorded = Tile("Recorded")
        self.t_busiest = Tile("Most CPU")
        self.t_biggest = Tile("Most memory")
        for tile in (self.t_recorded, self.t_busiest, self.t_biggest):
            tiles.addWidget(tile)
        layout.addLayout(tiles)
        self.apps_off = QLabel("", objectName="note")
        self.apps_off.setWordWrap(True)
        layout.addWidget(self.apps_off)

        find = QHBoxLayout()
        self.search = QLineEdit(placeholderText="Find an app (e.g. Chrome)…")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(320)
        self.search.textChanged.connect(lambda _: self.load())
        find.addWidget(self.search)
        find.addWidget(QLabel("Every app is listed (scroll). Click one to see it on the chart below.",
                              objectName="note"))
        find.addStretch()
        layout.addLayout(find)

        tables = QHBoxLayout()
        box = QGroupBox("CPU")
        v = QVBoxLayout(box)
        self.cpu_table = make_table(
            ["App", "Share of CPU work", "Average", "Peak"],
            {1: "Of all the CPU work your PC did this day, how much this app did",
             2: "Average CPU use while the app was open (% of the whole CPU)",
             3: "Its highest CPU use, and when"})
        self.cpu_table.cellClicked.connect(lambda r, c: self._focus_from(self.cpu_table, r))
        v.addWidget(self.cpu_table)
        tables.addWidget(box)
        box = QGroupBox("Memory")
        v = QVBoxLayout(box)
        self.mem_table = make_table(
            ["App", "Average", "Peak"],
            {1: "Average memory use while the app was open", 2: "Its highest memory use, and when"})
        self.mem_table.cellClicked.connect(lambda r, c: self._focus_from(self.mem_table, r))
        v.addWidget(self.mem_table)
        tables.addWidget(box)
        layout.addLayout(tables, stretch=3)

        self.chart_box = box = QGroupBox("Through the day (top 5 apps)")
        v = QVBoxLayout(box)
        row = QHBoxLayout()
        group = QButtonGroup(box)
        for label, metric in (("CPU", "cpu"), ("Memory", "mem")):
            button = QPushButton(label, checkable=True)
            button.setChecked(metric == self.app_metric)
            button.clicked.connect(lambda _=False, m=metric: self._set_app_metric(m))
            group.addButton(button)
            row.addWidget(button)
        self.top5 = QPushButton("Back to top 5")
        self.top5.clicked.connect(lambda: self._set_focus(None))
        self.top5.hide()
        row.addWidget(self.top5)
        row.addStretch()
        self.chart_note = QLabel("Scroll on the chart to zoom in", objectName="note")
        row.addWidget(self.chart_note)
        v.addLayout(row)
        self.app_plot = time_plot()
        self.app_plot.addLegend(offset=(10, 6), labelTextColor=T.TEXT, colCount=5)
        v.addWidget(self.app_plot)
        layout.addWidget(box, stretch=2)
        return tab

    def _build_network_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        tiles = QHBoxLayout()
        self.t_ping = Tile("Average ping")
        self.t_worst = Tile("Worst ping")
        self.t_loss = Tile("Packet loss")
        self.t_drops = Tile("Drops")
        self.t_offline = Tile("Time offline")
        for tile in (self.t_ping, self.t_worst, self.t_loss, self.t_drops, self.t_offline):
            tiles.addWidget(tile)
        layout.addLayout(tiles)
        self.net_off = QLabel("", objectName="note")
        self.net_off.setWordWrap(True)
        layout.addWidget(self.net_off)

        box = QGroupBox("Ping through the day  (red = internet dropped)")
        v = QVBoxLayout(box)
        self.ping_plot = time_plot()
        self.ping_plot.addLegend(offset=(10, 6), labelTextColor=T.TEXT)
        self.ping_plot.getPlotItem().setLabel("left", "ms", color=T.MUTED)
        v.addWidget(self.ping_plot, stretch=3)
        self.wifi_plot = time_plot(height=85)
        self.wifi_plot.getPlotItem().setLabel("left", "Wi-Fi %", color=T.MUTED)
        self.wifi_plot.setYRange(0, 100, padding=0)
        self.wifi_plot.setXLink(self.ping_plot)
        v.addWidget(self.wifi_plot)
        layout.addWidget(box, stretch=3)

        bottom = QHBoxLayout()
        box = QGroupBox("Drops")
        v = QVBoxLayout(box)
        self.events_table = make_table(["Likely cause", "Started", "Back at", "Lasted"])
        v.addWidget(self.events_table)
        bottom.addWidget(box, stretch=3)
        box = QGroupBox("Speed tests")
        v = QVBoxLayout(box)
        row = QHBoxLayout()
        self.speed_button = QPushButton("Run speed test now")
        self.speed_button.clicked.connect(self._run_speed_test)
        row.addWidget(self.speed_button)
        self.speed_status = QLabel(f"Uses about {netwatch.DATA_USED_MB} MB of data", objectName="note")
        row.addWidget(self.speed_status)
        row.addStretch()
        v.addLayout(row)
        self.speed_table = make_table(["Time", "Download", "Upload", "Ping"])
        v.addWidget(self.speed_table)
        bottom.addWidget(box, stretch=2)
        layout.addLayout(bottom, stretch=2)
        return tab

    def _build_games_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        row = QHBoxLayout()
        row.addWidget(QLabel("Every game session that day. Double-click one (or select it and press "
                             "Open recap) to see how your PC handled it.", objectName="note"))
        row.addStretch()
        self.open_recap = QPushButton("Open recap")
        self.open_recap.clicked.connect(self._open_selected_recap)
        row.addWidget(self.open_recap)
        layout.addLayout(row)
        self.games_table = make_table(["Game", "Started", "Played", "Average FPS", "Verdict"])
        self.games_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.games_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.games_table.setFocusPolicy(Qt.StrongFocus)
        self.games_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.games_table.cellDoubleClicked.connect(lambda r, c: self._open_recap_row(r))
        layout.addWidget(self.games_table, stretch=1)
        return tab

    def load_games(self, day: str):
        rows = sessions_on(self.con, day)
        self.game_ids = [r[0] for r in rows]
        out = []
        for sid, name, start, end in rows:
            if sid not in self.verdicts:
                s = load_session(self.con, sid)
                a = analyze(s.samples, s.meta)
                self.verdicts[sid] = (a.verdict, a.severity, a.stats.get("avg_fps"),
                                      a.stats.get("avg_rfps"), a.stats.get("frame_gen"))
            verdict, severity, fps, rfps, fg = self.verdicts[sid]
            fps_text = "—" if not fps else (f"{fps:.0f}  ({rfps:.0f} rendered)" if fg else f"{fps:.0f}")
            out.append((name, clock(start), fmt_seconds(end - start), fps_text, verdict, severity))
        fill_table(self.games_table, [r[:5] for r in out], "No game sessions this day")
        for i, r in enumerate(out):
            item = self.games_table.item(i, 4)
            item.setForeground(QColor(SEVERITY_COLORS[r[5]]))
            item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.open_recap.setEnabled(bool(out))

    def _open_recap_row(self, row: int):
        if 0 <= row < len(getattr(self, "game_ids", [])):
            self.dash.show_session_id(self.game_ids[row])

    def _open_selected_recap(self):
        rows = self.games_table.selectionModel().selectedRows()
        self._open_recap_row(rows[0].row() if rows else 0)

    # --- loading ----------------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start(LIVE_REFRESH_MS)
        self.refresh()

    def refresh(self):
        current = self.day.currentData()
        days = days_with_data(self.con)
        today = time.strftime("%Y-%m-%d")
        if today not in days:
            days.insert(0, today)
        self.day.blockSignals(True)
        self.day.clear()
        for d in days:
            self.day.addItem(day_label(d), d)
        index = self.day.findData(current) if current else 0
        self.day.setCurrentIndex(max(index, 0))
        self.day.blockSignals(False)
        self.load()

    def _auto_refresh(self):
        if self.isVisible() and not self.isMinimized() and self.is_today():
            self.load()

    def is_today(self) -> bool:
        return self.day.currentData() == time.strftime("%Y-%m-%d")

    def load(self):
        day = self.day.currentData()
        if not day:
            return
        st = self.dash.settings
        parts = [f"App tracker {'ON' if st.app_tracker else 'off'}",
                 f"Network watcher {'ON' if st.net_watcher else 'off'}"]
        if self.is_today() and (st.app_tracker or st.net_watcher):
            parts.append(f"updated {time.strftime('%H:%M:%S')}")
        self.status.setText("  ·  ".join(parts))
        self.load_apps(day)
        self.load_network(day)
        self.load_games(day)
        self.shown_day, self.shown_metric = day, self.app_metric

    def restore_range(self, plot: pg.PlotWidget, start: int, end: int, previous):
        """Show the whole day, unless you've zoomed in on this same day: then stay zoomed."""
        if previous and self.shown_day == self.day.currentData():
            lo, hi = previous
            if abs(lo - start) > 60 or abs(hi - end) > 60:
                plot.setXRange(lo, hi, padding=0)
                return
        plot.setXRange(start, end, padding=0)

    def live_apps(self, day: str):
        """The tracker's minute in progress, if it belongs to this day."""
        recorder = self.dash.tracker
        live = recorder.live if recorder else None
        if not live or time.strftime("%Y-%m-%d", time.localtime(live[0])) != day:
            return None, {}
        return live[0], live[1]

    def live_network(self, day: str):
        watcher = self.dash.netwatch
        live = watcher.live if watcher else None
        if not live or time.strftime("%Y-%m-%d", time.localtime(live[0])) != day:
            return []
        minute, stats = live
        return [(minute, target, sent, lost, ms_sum / (sent - lost) if sent > lost else None,
                 max_ms if sent > lost else None)
                for target, (sent, lost, ms_sum, max_ms) in stats.items()]

    def _set_app_metric(self, metric: str):
        self.app_metric = metric
        self.load()

    def _focus_from(self, table: QTableWidget, row: int):
        item = table.item(row, 0)
        app = item.data(Qt.UserRole) if item else None
        if app:
            self._set_focus(None if app == self.focus_app else app)  # click again to go back

    def _set_focus(self, app: str | None):
        self.focus_app = app
        self.shown_metric = None  # show the whole day for the new chart
        self.load()

    def load_apps(self, day: str):
        start, end = day_bounds(day)
        minutes = self.con.execute("SELECT COUNT(*) FROM tracked_minute WHERE ts >= ? AND ts < ?",
                                   (start, end)).fetchone()[0]
        rows = self.con.execute(
            "SELECT app, samples, cpu_sum, cpu_peak, cpu_peak_at, mem_sum, mem_peak, mem_peak_at, "
            "cpu_seconds FROM app_day WHERE day = ?", (day,)).fetchall()
        tracker_on = self.dash.settings.app_tracker
        self.apps_off.setVisible(not tracker_on)
        self.apps_off.setText("The app tracker is off, so nothing new is being recorded. Turn it on "
                              "in Settings → Tracking & reports.")

        # Add the minute that's still being collected, so today's numbers move live.
        live_minute, live = self.live_apps(day)
        if live:
            minutes += 1
            cores, step = self.dash.tracker.cores, tracker.SAMPLE_SECONDS
            merged = {r[0]: list(r) for r in rows}
            for app, (n, cpu_sum, cpu_peak, cpu_at, mem_sum, mem_peak, mem_at) in live.items():
                r = merged.setdefault(app, [app, 0, 0.0, -1.0, cpu_at, 0.0, -1.0, mem_at, 0.0])
                r[1] += n
                r[2] += cpu_sum
                if cpu_peak > r[3]:
                    r[3], r[4] = cpu_peak, cpu_at
                r[5] += mem_sum
                if mem_peak > r[6]:
                    r[6], r[7] = mem_peak, mem_at
                r[8] += cpu_sum / 100 * cores * step
            rows = [tuple(r) for r in merged.values()]

        total = sum(r[8] for r in rows) or 1
        by_cpu = sorted(rows, key=lambda r: r[8], reverse=True)
        by_mem = sorted(rows, key=lambda r: r[5] / r[1], reverse=True)
        wanted = self.search.text().strip().lower()

        def shown(ranked):  # (rank, row), filtered by the search box
            return [(i + 1, r) for i, r in enumerate(ranked) if wanted in r[0].lower()]
        cpu_rows, mem_rows = shown(by_cpu), shown(by_mem)
        empty = f'No app matching "{self.search.text()}"' if wanted else "Nothing recorded"
        fill_table(self.cpu_table, [
            (f"{n}.  {r[0]}", f"{r[8] / total * 100:.1f}%" if r[8] / total < 0.01 else
             f"{r[8] / total * 100:.0f}%", f"{r[2] / r[1]:.2f}%" if r[2] / r[1] < 1 else
             f"{r[2] / r[1]:.1f}%", f"{r[3]:.1f}% at {clock(r[4])}") for n, r in cpu_rows], empty)
        fill_table(self.mem_table, [
            (f"{n}.  {r[0]}", fmt_bytes(r[5] / r[1]), f"{fmt_bytes(r[6])} at {clock(r[7])}")
            for n, r in mem_rows], empty)
        for table, listed in ((self.cpu_table, cpu_rows), (self.mem_table, mem_rows)):
            for i, (_, r) in enumerate(listed):
                table.item(i, 0).setData(Qt.UserRole, r[0])  # the app's name, for clicking
                if r[0] == self.focus_app:
                    for c in range(table.columnCount()):
                        table.item(i, c).setForeground(QColor(T.TITLE))
                        font = table.item(i, c).font()
                        font.setBold(True)
                        table.item(i, c).setFont(font)
        self.t_recorded.set(fmt_minutes(minutes), "while PC Monitor was running")
        if by_cpu:
            self.t_busiest.set(by_cpu[0][0], f"{by_cpu[0][8] / total * 100:.0f}% of all CPU work")
            self.t_biggest.set(by_mem[0][0], f"about {fmt_bytes(by_mem[0][5] / by_mem[0][1])} on average")
        else:
            self.t_busiest.set("—")
            self.t_biggest.set("—")

        plot = self.app_plot
        previous = plot.viewRange()[0] if self.shown_metric == self.app_metric else None
        plot.clear()
        names = {r[0] for r in rows}
        if self.focus_app and self.focus_app not in names:
            self.focus_app = None  # e.g. a different day where it didn't run
        top = ([self.focus_app] if self.focus_app else
               [r[0] for r in (by_cpu if self.app_metric == "cpu" else by_mem)[:5]])
        self.top5.setVisible(self.focus_app is not None)
        self.chart_box.setTitle(f"Through the day: {self.focus_app}" if self.focus_app
                                else "Through the day (top 5 apps)")
        partial = self.focus_app and self.focus_app.lower() not in tracker.my_app_names()
        self.chart_note.setText("Detail is only kept for each minute's busiest apps (and your own "
                                "apps), so gaps mean it was quiet" if partial
                                else "Scroll on the chart to zoom in")
        unit = "%"
        if top:
            marks = ",".join("?" * len(top))
            col = "cpu" if self.app_metric == "cpu" else "mem"
            data = self.con.execute(
                f"SELECT ts, app, {col} FROM app_minute WHERE ts >= ? AND ts < ? AND app IN ({marks}) "
                "ORDER BY ts", (start, end, *top)).fetchall()
            for app, a in live.items():  # the minute in progress
                if app in top:
                    data.append((live_minute, app, a[1] / a[0] if col == "cpu" else a[4] / a[0]))
            biggest = max((v for _, _, v in data), default=0)
            scale, unit = ((1, "% CPU") if col == "cpu" else
                           (1024**3, "GB") if biggest >= 1024**3 else (1024**2, "MB"))
            for i, app in enumerate(top):
                points = [(ts, v / scale) for ts, a, v in data if a == app]
                xs, ys = gapped(bucketed(points), gap=1500)
                dots = {"symbol": "o", "symbolSize": 7, "symbolBrush": LINE_COLORS[i],
                        "symbolPen": None} if len(xs) < 3 else {}  # too few points for a line yet
                plot.plot(xs, ys, name=app, pen=pg.mkPen(LINE_COLORS[i], width=2), connect="finite",
                          **dots)
        plot.getPlotItem().setLabel("left", unit, color=T.MUTED)
        self.restore_range(plot, start, end, previous)
        plot.enableAutoRange(axis="y")

    def load_network(self, day: str):
        start, end = day_bounds(day)
        minutes = self.con.execute(
            "SELECT ts, target, sent, lost, avg_ms, max_ms FROM net_minute "
            "WHERE ts >= ? AND ts < ? ORDER BY ts", (start, end)).fetchall()
        events = self.con.execute(
            "SELECT start, end, kind FROM net_event WHERE start >= ? AND start < ? ORDER BY start",
            (start, end)).fetchall()
        watcher_on = self.dash.settings.net_watcher
        self.net_off.setVisible(not watcher_on)
        self.net_off.setText("The network watcher is off, so nothing new is being recorded. Turn it on "
                             "in Settings → Tracking & reports. Speed tests work either way.")
        minutes = minutes + self.live_network(day)  # include the minute in progress
        now = int(time.time())

        internet = [m for m in minutes if m[1] == "internet"]
        sent = sum(m[2] for m in internet)
        lost = sum(m[3] for m in internet)
        ok_weighted = [(m[4], m[2] - m[3]) for m in internet if m[4] is not None]
        ok = sum(n for _, n in ok_weighted)
        avg = sum(ms * n for ms, n in ok_weighted) / ok if ok else None
        worst = max((m[5] for m in internet if m[5] is not None), default=None)
        # A drop with no end yet is happening right now (if the watcher is running).
        ongoing_end = now if self.dash.netwatch else None
        router_events = [(s, e or ongoing_end or s) for s, e, k in events if k == "router"]
        drops = []
        for s, e, kind in events:
            if kind != "internet":
                continue
            still_down = e is None and ongoing_end is not None
            e = e or ongoing_end or s
            local = any(rs <= e and re_ >= s for rs, re_ in router_events)
            drops.append(("Wi-Fi or router" if local else "Internet provider", s, e, still_down))
        offline = sum(e - s for _, s, e, _ in drops)

        self.t_ping.set(f"{avg:.0f} ms" if avg is not None else "—", "to the internet")
        self.t_worst.set(f"{worst:.0f} ms" if worst is not None else "—", "slowest single ping")
        loss = lost / sent * 100 if sent else None
        self.t_loss.set(f"{loss:.1f}%" if loss is not None else "—", "pings with no answer",
                        HOT if loss and loss >= 2 else None)
        self.t_drops.set(str(len(drops)) if sent else "—", "times the internet went away",
                         HOT if drops else None)
        self.t_offline.set(fmt_seconds(offline) if sent else "—", "in total",
                           HOT if offline else None)

        plot = self.ping_plot
        previous = plot.viewRange()[0]
        plot.clear()
        for target, name, color in (("internet", "Internet (1.1.1.1)", "#4f9dff"),
                                    ("router", "Your router", "#3dd68c")):
            points = [(m[0], m[4]) for m in minutes if m[1] == target]
            if points:
                xs, ys = gapped(points)
                plot.plot(xs, ys, name=name, pen=pg.mkPen(color, width=2), connect="finite")
        for _, s, e, _ in drops:
            region = pg.LinearRegionItem(values=(s, max(e, s + 30)), movable=False,
                                         brush=pg.mkBrush(255, 92, 92, 70),
                                         pen=pg.mkPen(255, 92, 92, 160))
            plot.addItem(region)
        self.restore_range(plot, start, end, previous)
        plot.enableAutoRange(axis="y")

        self.wifi_plot.clear()
        wifi = self.con.execute("SELECT ts, signal FROM wifi_minute WHERE ts >= ? AND ts < ? ORDER BY ts",
                                (start, end)).fetchall()
        watcher = self.dash.netwatch
        if watcher and watcher.signal is not None and self.is_today():
            wifi.append((now, watcher.signal))  # right now
        if wifi:
            xs, ys = gapped(wifi)
            self.wifi_plot.plot(xs, ys, pen=pg.mkPen("#b38bff", width=2), connect="finite")

        fill_table(self.events_table,
                   [(cause, clock(s), "still down" if down else clock(e),
                     fmt_seconds(e - s) + ("+" if down else ""))
                    for cause, s, e, down in drops],
                   "No drops recorded" if sent else "Nothing recorded")
        tests = self.con.execute("SELECT ts, down_mbps, up_mbps, ping_ms FROM speed_test "
                                 "WHERE ts >= ? AND ts < ? ORDER BY ts DESC", (start, end)).fetchall()
        fill_table(self.speed_table, [(clock(ts), f"{d:.0f} Mbit/s", f"{u:.0f} Mbit/s", f"{p:.0f} ms")
                                      for ts, d, u, p in tests], "No speed tests this day")

    # --- speed test -----------------------------------------------------------
    def _run_speed_test(self):
        if self.speed_running:
            return
        self.speed_running = True
        self.speed_button.setEnabled(False)

        def work():
            try:
                result = netwatch.speed_test(self.signals.progress.emit)
            except Exception as exc:  # no internet, Cloudflare unreachable, etc.
                result = exc
            self.signals.done.emit(result)

        threading.Thread(target=work, daemon=True, name="speed-test").start()

    def _speed_progress(self, text: str):
        self.speed_status.setText(text)

    def _speed_done(self, result):
        self.speed_running = False
        self.speed_button.setEnabled(True)
        if isinstance(result, Exception):
            self.speed_status.setText(f"Speed test failed: {result}")
            return
        self.speed_status.setText(f"Done: {result['down']:.0f} down / {result['up']:.0f} up Mbit/s, "
                                  f"{result['ping']:.0f} ms")
        today = time.strftime("%Y-%m-%d")
        if self.day.currentData() != today:
            self.day.setCurrentIndex(max(self.day.findData(today), 0))
        self.load()

    def hideEvent(self, event):
        self.timer.stop()  # no refreshing while you're on another screen
        super().hideEvent(event)
