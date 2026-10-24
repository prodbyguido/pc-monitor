"""Colour themes. The theme is picked once at startup; changing it in Settings restarts the app."""
from status import GOOD

THEMES = {
    "Dark": {
        "BG": "#06080b", "CARD": "#1c2331", "PLOT_BG": "#131925", "BORDER": "#45526b",
        "TEXT": "#f4f6fa", "TITLE": "#e3e8f2", "MUTED": "#b3bccd", "BUTTON": "#2a3346",
        "GRID_ALPHA": 0.22,
    },
    "High contrast": {
        "BG": "#000000", "CARD": "#222b3d", "PLOT_BG": "#0b0f17", "BORDER": "#8a9bbd",
        "TEXT": "#ffffff", "TITLE": "#ffffff", "MUTED": "#dde3ee", "BUTTON": "#34405a",
        "GRID_ALPHA": 0.32,
    },
    "Midnight": {
        "BG": "#030712", "CARD": "#101a33", "PLOT_BG": "#0a1124", "BORDER": "#34508a",
        "TEXT": "#eef3ff", "TITLE": "#dbe6ff", "MUTED": "#a9b9da", "BUTTON": "#1d2c52",
        "GRID_ALPHA": 0.22,
    },
    "OLED black": {
        "BG": "#000000", "CARD": "#000000", "PLOT_BG": "#06080c", "BORDER": "#3d4658",
        "TEXT": "#f4f6fa", "TITLE": "#e3e8f2", "MUTED": "#a7b0c2", "BUTTON": "#1a1f2a",
        "GRID_ALPHA": 0.2,
    },
}


class Theme:
    def use(self, name: str):
        self.name = name if name in THEMES else "Dark"
        self.__dict__.update(THEMES[self.name])


T = Theme()
T.use("Dark")


def stylesheet() -> str:
    return f"""
QWidget#root {{ background: {T.BG}; }}
QFrame#card {{ background: {T.CARD}; border: 2px solid {T.BORDER}; border-radius: 12px; }}
QLabel {{ color: {T.TEXT}; background: transparent; border: none; }}
QLabel#title {{ color: {T.TITLE}; font-size: 13pt; font-weight: 700; letter-spacing: 1px; }}
QLabel#value {{ font-size: 22pt; font-weight: 700; }}
QLabel#footer, QLabel#peak {{ color: {T.MUTED}; font-size: 10.5pt; }}
QLabel#subname {{ color: {T.TITLE}; font-size: 11pt; font-weight: 700; }}
QLabel#subvalue {{ font-size: 15pt; font-weight: 700; }}
QLabel#heading {{ font-size: 16pt; font-weight: 700; }}
QLabel#subheading {{ color: {T.MUTED}; font-size: 11pt; }}
QLabel#badge {{ border-radius: 6px; padding: 2px 8px; font-size: 9pt; font-weight: 800; }}
QLabel#note {{ color: {T.MUTED}; font-size: 9.5pt; }}

QLabel#mininame {{ color: {T.MUTED}; font-size: 9pt; font-weight: 700; }}
QLabel#minivalue {{ font-size: 16pt; font-weight: 700; }}

QPushButton {{ background: {T.BUTTON}; color: {T.TEXT}; border: 1px solid {T.BORDER};
              border-radius: 6px; padding: 4px 12px; font-size: 10.5pt; }}
QPushButton:hover {{ border-color: {T.TITLE}; }}
QPushButton:checked {{ background: {T.BORDER}; font-weight: 700; }}
QPushButton:disabled {{ color: {T.MUTED}; }}

QPushButton#nav {{ padding: 5px 16px; font-size: 11pt; font-weight: 600; }}
QPushButton#updatebadge {{ background: {GOOD}; color: #0b0d12; border: none; font-weight: 800; }}
QPushButton#nav:checked {{ background: {T.TITLE}; color: {T.BG}; border-color: {T.TITLE}; font-weight: 800; }}

QLabel#verdict {{ font-size: 15pt; font-weight: 800; border-radius: 10px; padding: 8px 16px; }}
QWidget#findings {{ background: {T.CARD}; }}
QLabel#findingtitle {{ font-size: 12pt; font-weight: 800; }}
QLabel#fix {{ color: {T.TITLE}; }}
QWidget#pagebody {{ background: {T.BG}; }}
QScrollArea {{ background: transparent; border: none; }}
QGroupBox {{ color: {T.TITLE}; font-size: 12pt; font-weight: 700; border: 2px solid {T.BORDER};
            border-radius: 10px; margin-top: 16px; padding: 14px 10px 8px 10px;
            background: {T.CARD}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; }}
QCheckBox {{ color: {T.TEXT}; font-size: 11pt; spacing: 10px; }}
QCheckBox:disabled {{ color: {T.MUTED}; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border: 2px solid {T.BORDER};
                       border-radius: 4px; background: {T.PLOT_BG}; }}
QCheckBox::indicator:checked {{ background: {GOOD}; border-color: {GOOD}; }}
QCheckBox::indicator:disabled {{ background: {T.CARD}; }}
QComboBox, QSpinBox, QLineEdit {{ background: {T.PLOT_BG}; color: {T.TEXT};
                      border: 1px solid {T.BORDER}; border-radius: 6px; padding: 3px 8px;
                      font-size: 11pt; }}
QLineEdit:focus {{ border-color: {T.TITLE}; }}
QComboBox QAbstractItemView {{ background: {T.CARD}; color: {T.TEXT};
                              selection-background-color: {T.BORDER}; }}
QSlider::groove:horizontal {{ height: 6px; background: {T.PLOT_BG}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {T.TITLE}; width: 16px; margin: -6px 0; border-radius: 8px; }}
QMenu {{ background: {T.CARD}; color: {T.TEXT}; border: 1px solid {T.BORDER}; font-size: 10.5pt; }}
QMenu::item {{ padding: 5px 22px; }}
QMenu::item:selected {{ background: {T.BORDER}; }}
QToolTip {{ background: {T.CARD}; color: {T.TEXT}; border: 1px solid {T.BORDER}; }}

QWidget#reports {{ background: {T.BG}; }}
QLabel#tilevalue {{ font-size: 18pt; font-weight: 700; }}
QTabWidget::pane {{ border: 2px solid {T.BORDER}; border-radius: 10px; top: -2px; background: {T.CARD}; }}
QTabBar::tab {{ background: {T.BUTTON}; color: {T.TEXT}; padding: 8px 22px; margin-right: 4px;
               border: 2px solid {T.BORDER}; border-bottom: none; border-top-left-radius: 8px;
               border-top-right-radius: 8px; font-size: 11.5pt; font-weight: 600; }}
QTabBar::tab:selected {{ background: {T.CARD}; color: {T.TITLE}; font-weight: 800; }}
QTableWidget {{ background: {T.PLOT_BG}; alternate-background-color: {T.CARD}; color: {T.TEXT};
               border: 1px solid {T.BORDER}; border-radius: 6px; font-size: 10.5pt; }}
QTableWidget::item {{ padding: 3px 8px; }}
QHeaderView::section {{ background: {T.BUTTON}; color: {T.TITLE}; padding: 6px 8px; border: none;
                       font-size: 10pt; font-weight: 700; }}
QScrollBar:vertical {{ background: {T.PLOT_BG}; width: 12px; }}
QScrollBar::handle:vertical {{ background: {T.BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""
