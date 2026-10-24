"""Building blocks for the dashboard: cards, scrolling graphs and animated numbers."""
import weakref
from collections import deque
from itertools import islice

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
import pyqtgraph as pg

from status import GOOD, HOT
from theme import T

MAX_HISTORY = 3600  # samples kept per graph: 1 hour at one sample per second


class Motion:
    """Whether numbers ease and graphs glide between samples (Settings → Smooth animation)."""
    smooth = True


def apply_color(curve, color: str, width: float = 2.0):
    fill = QColor(color)
    fill.setAlpha(50)
    curve.setPen(pg.mkPen(color, width=width))
    curve.setBrush(fill)


def style_plot(plot: pg.PlotItem):
    plot.setMouseEnabled(False, False)
    plot.setMenuEnabled(False)
    plot.hideButtons()
    plot.hideAxis("bottom")
    plot.getViewBox().setBackgroundColor(T.PLOT_BG)


class AnimatedLabel(QLabel):
    """A number that eases toward its latest value instead of jumping."""

    instances = weakref.WeakSet()

    def __init__(self, object_name: str):
        super().__init__("—", objectName=object_name)
        self.target = self.shown = None
        self.fmt = str
        self.color = None
        AnimatedLabel.instances.add(self)

    def set_value(self, value: float | None, fmt=None, color: str | None = None):
        if fmt:
            self.fmt = fmt
        if color and color != self.color:
            self.color = color
            self.setStyleSheet(f"color: {color};")
        if value is None:
            self.target = self.shown = None
            self.setText("—")
            return
        self.target = float(value)
        if self.shown is None or not Motion.smooth:
            self.shown = self.target
            self.setText(self.fmt(self.shown))

    def step(self):
        if self.target is None or self.shown == self.target:
            return
        diff = self.target - self.shown
        self.shown = self.target if abs(diff) < 0.05 else self.shown + diff * 0.2
        text = self.fmt(self.shown)
        if text != self.text():
            self.setText(text)


class Series:
    """One line on a plot: keeps up to an hour of history and shows the chosen time range.

    The newest sample sits at x=0 and older ones at negative x. When smooth animation is on,
    the line is nudged right by one sample on each update and glides back, so it scrolls
    continuously instead of jumping once a second.
    """

    def __init__(self, plot: pg.PlotItem, y_range=None, min_top: float = 1.0, width: float = 2.0):
        self.plot = plot
        self.y_range = y_range
        self.min_top = min_top
        self.width = width
        self.color = None
        self.peak = None
        self.peak_visible = False
        self.view = 120
        self._offset = None
        self.data = deque(maxlen=MAX_HISTORY)
        plot.setClipToView(True)
        plot.setDownsampling(auto=True, mode="peak")
        self.curve = plot.plot(fillLevel=0)
        self.peak_line = pg.InfiniteLine(angle=0, movable=False,
                                         pen=pg.mkPen(T.MUTED, width=1, style=Qt.DashLine))
        self.peak_line.hide()
        plot.addItem(self.peak_line, ignoreBounds=True)
        if y_range:
            plot.setYRange(*y_range, padding=0)
        self.set_view(self.view)
        self.set_status(GOOD)

    def set_view(self, seconds: int):
        self.view = seconds
        self.plot.setXRange(-(seconds - 1), 0, padding=0)
        self._redraw()

    def set_y_range(self, y_range):
        self.y_range = y_range
        self.plot.setYRange(*y_range, padding=0)

    def push(self, value: float):
        self.data.append(value)
        if self.peak is None or value > self.peak:
            self.peak = value
        self.peak_line.setValue(self.peak)
        self.peak_line.setVisible(self.peak_visible)
        self.glide(0 if Motion.smooth else 1)
        self._redraw()

    def _redraw(self):
        n = min(len(self.data), self.view + 1)
        if not n:
            return
        ys = list(islice(self.data, len(self.data) - n, None))
        self.curve.setData(range(1 - n, 1), ys)
        if not self.y_range:
            self.plot.setYRange(0, max(max(ys) * 1.15, self.min_top), padding=0)

    def glide(self, fraction: float):
        """fraction: 0 just after a new sample, 1 when the next one is due.

        Only moves the line when it would shift by about half a pixel or more, so graphs
        are redrawn a few times a second instead of every animation frame.
        """
        fraction = min(max(fraction, 0.0), 1.0)
        sample_px = self.plot.getViewBox().width() / max(self.view, 1)
        steps = max(1, round(sample_px * 2))
        offset = 1 - round(fraction * steps) / steps
        if offset != self._offset:
            self._offset = offset
            self.curve.setPos(offset, 0)

    def set_status(self, color: str) -> bool:
        if color == self.color:
            return False
        self.color = color
        apply_color(self.curve, color, self.width)
        return True

    def set_peak_visible(self, on: bool):
        self.peak_visible = on
        self.peak_line.setVisible(on and self.peak is not None)

    def reset_peak(self):
        self.peak = None
        self.peak_line.hide()


class Graph(pg.PlotWidget):
    def __init__(self, y_range=None, min_top: float = 1.0, axis: bool = True):
        super().__init__(background=T.CARD)
        plot = self.getPlotItem()
        style_plot(plot)
        if axis:
            plot.showGrid(x=False, y=True, alpha=T.GRID_ALPHA)
            left = plot.getAxis("left")
            left.setTextPen(T.MUTED)
            left.setPen(T.BORDER)
            left.setWidth(44)
            left.setStyle(tickFont=QFont("Segoe UI", 9))
        else:
            plot.hideAxis("left")
        # Graphs take whatever height is left, so the window can be as short as you like.
        self.setMinimumHeight(28)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        self.series = Series(plot, y_range, min_top)


class Card(QFrame):
    """Rounded panel: title, optional badge, big value, content, footer and peak text."""

    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("card")
        # The grid sizes cards purely by its column/row stretch, never by their text, so tiles
        # keep the same size when numbers get longer or shorter.
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.title = QLabel(title.upper(), objectName="title")
        self.badge = QLabel(objectName="badge")
        self.badge.hide()
        self.value = AnimatedLabel("value")
        self.footer = QLabel("", objectName="footer")
        self.footer.setTextFormat(Qt.RichText)
        self.peak = QLabel("", objectName="peak")
        # Let long text get clipped rather than force the grid columns to different widths.
        for label in (self.title, self.footer):
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.alerting = False
        self._border = None

        header = QHBoxLayout()
        header.addWidget(self.title, stretch=1, alignment=Qt.AlignTop)
        header.addWidget(self.value)

        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 12, 16, 12)
        self.body.setSpacing(6)
        self.body.addLayout(header)

    def add_content(self, widget: QWidget):
        self.body.addWidget(widget, stretch=1)
        bottom = QHBoxLayout()
        bottom.addWidget(self.footer, stretch=1)
        bottom.addWidget(self.badge)
        bottom.addSpacing(8)
        bottom.addWidget(self.peak)
        self.body.addLayout(bottom)

    def series_list(self) -> list[Series]:
        return []

    def set_badge(self, text: str | None, color: str = HOT):
        if not text:
            self.badge.hide()
            return
        style = f"background: {color}; color: #0b0d12;"
        if self.badge.text() != text or self.badge.styleSheet() != style:
            self.badge.setText(text)
            self.badge.setStyleSheet(style)
        self.badge.show()

    def show_peaks(self, on: bool):
        on = on and not getattr(self, "no_peaks", False)
        self.peak.setVisible(on)
        for s in self.series_list():
            s.set_peak_visible(on)

    def reset_peaks(self):
        for s in self.series_list():
            s.reset_peak()
        self.peak.setText("")

    def set_alerting(self, on: bool):
        self.alerting = on
        if not on:
            self._set_border(None)

    def pulse(self, phase: bool):
        if self.alerting:
            self._set_border(HOT if phase else None)

    def _set_border(self, color: str | None):
        if color == self._border:
            return
        self._border = color
        self.setStyleSheet(f"QFrame#card {{ border-color: {color}; }}" if color else "")


class GraphCard(Card):
    """Card with one graph; the graph and the big value take the reading's status colour."""

    def __init__(self, title: str, y_range=None, min_top: float = 1.0):
        super().__init__(title)
        self.graph = Graph(y_range, min_top)
        self.series = self.graph.series
        self.add_content(self.graph)

    def series_list(self):
        return [self.series]

    def update(self, value: float, fmt, color: str):
        self.series.push(value)
        self.series.set_status(color)
        self.value.set_value(value, fmt, color)
        self.peak.setText(f"Peak {fmt(self.series.peak)}")

    def set_y_max(self, top: float):
        self.series.set_y_range((0, top))


class SplitGraphCard(Card):
    """Card with two side-by-side graphs, each with its own name, value, peak and scale."""

    def __init__(self, title: str, names: tuple[str, str], min_top: float = 1.0,
                 footer: bool = False):
        super().__init__(title)
        self.value.hide()
        self.peak.hide()
        self.footer.setVisible(footer)
        content = QWidget()
        layout = QHBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.graphs, self.values, self.peaks = [], [], []
        for name in names:
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(QLabel(name, objectName="subname"))
            value = AnimatedLabel("subvalue")
            value.set_value(None, color=GOOD)
            col.addWidget(value)
            col.addSpacing(4)
            graph = Graph(min_top=min_top, axis=False)
            col.addWidget(graph, stretch=1)
            col.addSpacing(4)
            peak = QLabel("", objectName="peak")
            col.addWidget(peak)
            for label in (value, peak):
                label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            layout.addLayout(col, stretch=1)
            self.graphs.append(graph)
            self.values.append(value)
            self.peaks.append(peak)
        self.add_content(content)

    def series_list(self):
        return [g.series for g in self.graphs]

    def update(self, values: tuple, fmt, color: str | tuple = GOOD, show: tuple | None = None,
               notes: tuple | None = None):
        """values go on the graphs; show (if given) is what the numbers say, None = "—".
        color can be one colour or one per side. notes (if given) replace the peak text."""
        colors = color if isinstance(color, tuple) else (color, color)
        for i, (graph, label, peak, v) in enumerate(zip(self.graphs, self.values, self.peaks, values)):
            graph.series.push(v)
            graph.series.set_status(colors[i])
            label.set_value(v if show is None else show[i], fmt, colors[i])
            if notes is not None:  # trim long names with "…" rather than cutting them off
                width = peak.width() if peak.width() > 20 else 120
                peak.setText(peak.fontMetrics().elidedText(notes[i], Qt.ElideRight, width))
            else:
                peak.setText(f"Peak {fmt(graph.series.peak)}" if graph.series.peak else "")

    def use_notes(self):
        """Show a line of text under each graph (e.g. an app's name) instead of its peak."""
        self.notes_mode = True
        for peak in self.peaks:
            peak.setObjectName("subname")
            peak.style().unpolish(peak)
            peak.style().polish(peak)
            peak.show()
        for s in self.series_list():
            s.set_peak_visible(False)

    def show_peaks(self, on: bool):
        if getattr(self, "notes_mode", False):
            return
        for s, peak in zip(self.series_list(), self.peaks):
            s.set_peak_visible(on)
            peak.setVisible(on)

    def reset_peaks(self):
        for s, peak in zip(self.series_list(), self.peaks):
            s.reset_peak()
            peak.setText("")


class PerCoreCard(Card):
    """Grid of small graphs, one per logical processor, each coloured by its own load."""

    def __init__(self, count: int, physical: int):
        super().__init__("CPU usage per core" if count == physical else "CPU usage per thread")
        self.value.setText(f"{physical}C / {count}T")
        self.value.setStyleSheet(f"font-size: 13pt; color: {T.MUTED};")
        self.peak.hide()
        self.widget = pg.GraphicsLayoutWidget()
        self.widget.setMinimumHeight(40)
        self.widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        self.widget.setBackground(T.CARD)
        self.widget.ci.setSpacing(4)
        self.labels, self.series = [], []
        cols = 4 if count <= 16 else 8
        font = QFont("Segoe UI", 9)
        font.setBold(True)
        for i in range(count):
            p = self.widget.addPlot(row=i // cols, col=i % cols)
            style_plot(p)
            p.hideAxis("left")
            p.setContentsMargins(0, 0, 0, 0)
            # Label drawn inside the graph's top-left corner so it takes no extra space.
            label = pg.TextItem(anchor=(0, 0), color=T.TITLE)
            label.setFont(font)
            label.setParentItem(p.getViewBox())
            label.setPos(4, 2)
            self.labels.append(label)
            self.series.append(Series(p, y_range=(0, 105), width=1.5))
        self.add_content(self.widget)
        self.footer.setText("CPU 0–7, numbered like Task Manager" if count == 8
                            else "Numbered like Task Manager")

    def series_list(self):
        return self.series

    def show_peaks(self, on: bool):
        pass  # too small for peak lines

    def update(self, values: list[float], colors: list[str]):
        for i, (label, s, v, c) in enumerate(zip(self.labels, self.series, values, colors)):
            s.push(v)
            s.set_status(c)
            label.setText(f"{i}  {v:.0f}%")
