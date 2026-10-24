"""Tray icon showing the live CPU temperature, with a menu for the main actions."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


def number_icon(text: str, color: str) -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(0, 0, 64, 64), 14, 14)
    font = QFont("Segoe UI")
    font.setPixelSize(42 if len(text) <= 2 else 30)
    font.setBold(True)
    p.setFont(font)
    p.setPen(QColor("#0b0d12"))
    p.drawText(pm.rect(), Qt.AlignCenter, text)
    p.end()
    return QIcon(pm)


class Tray(QSystemTrayIcon):
    def __init__(self, dash):
        super().__init__(dash)
        self.dash = dash
        self._shown = None
        self.menu = QMenu()
        self.menu.addAction("Show monitor", lambda *_: dash.show_main())
        self.mini_action = self.menu.addAction("Mini mode", lambda *_: dash.toggle_mini())
        self.mini_action.setCheckable(True)
        self.overlay_action = self.menu.addAction("Overlay mode", lambda *_: dash.toggle_overlay())
        self.overlay_action.setCheckable(True)
        self.summary_action = self.menu.addAction("Last game recap",
                                                  lambda *_: dash.show_last_summary())
        self.menu.addAction("Reports", lambda *_: dash.open_reports())
        self.menu.addAction("Settings", lambda *_: dash.open_settings())
        self.menu.addSeparator()
        self.menu.addAction("Quit PC Monitor", lambda *_: dash.close())
        self.setContextMenu(self.menu)
        self.activated.connect(self._clicked)
        self.set_reading(None, "#8b94a7", "PC Monitor")

    def _clicked(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.dash.show_main()

    def sync(self):
        self.mini_action.setChecked(self.dash.in_mini)
        self.overlay_action.setChecked(self.dash.settings.overlay)
        self.summary_action.setEnabled(self.dash.has_recap)

    def set_reading(self, text: str | None, color: str, tooltip: str):
        key = (text, color)
        if key != self._shown:
            self._shown = key
            self.setIcon(number_icon(text or "--", color))
        self.setToolTip(tooltip)
