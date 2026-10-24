"""Mini mode: a small always-on-top strip with just the key numbers."""
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from theme import T
from widgets import AnimatedLabel

ITEMS = (("cpu_temp", "CPU TEMP"), ("gpu_temp", "GPU TEMP"), ("fps", "FPS"),
         ("cpu", "CPU LOAD"), ("gpu", "GPU LOAD"))


class MiniBar(QWidget):
    def __init__(self, settings, on_exit):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setObjectName("mini")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.settings = settings
        self.on_exit = on_exit
        self._drag: QPoint | None = None
        self.setToolTip("Drag to move  ·  double-click to go back to the full monitor")

        row = QHBoxLayout(self)
        row.setContentsMargins(18, 8, 18, 10)
        row.setSpacing(20)
        self.values, self.boxes = {}, {}
        for key, name in ITEMS:
            box = QWidget()
            col = QVBoxLayout(box)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(0)
            col.addWidget(QLabel(name, objectName="mininame"), alignment=Qt.AlignHCenter)
            value = AnimatedLabel("minivalue")
            value.setMinimumWidth(84)
            value.setAlignment(Qt.AlignCenter)
            col.addWidget(value)
            row.addWidget(box)
            self.values[key] = value
            self.boxes[key] = box

    def paintEvent(self, event):
        # Rounded panel drawn by hand: style sheets don't paint translucent top-level windows.
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(T.BORDER), 2))
        p.setBrush(QColor(T.CARD))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 10, 10)

    def set(self, key: str, value, fmt, color: str):
        self.values[key].set_value(value, fmt, color)

    def show_item(self, key: str, on: bool):
        self.boxes[key].setVisible(on)
        self.adjustSize()

    def place(self, main: QWidget):
        self.adjustSize()
        pos = QPoint(self.settings.mini_x, self.settings.mini_y)
        if self.settings.mini_x < 0 or not QApplication.screenAt(pos):
            screen = (main.screen() or QApplication.primaryScreen()).availableGeometry()
            pos = QPoint(screen.right() - self.width() - 20, screen.top() + 20)
        self.move(pos)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            self._drag = None
            self.settings.mini_x, self.settings.mini_y = self.x(), self.y()
            self.settings.save()

    def mouseDoubleClickEvent(self, event):
        self.on_exit()
