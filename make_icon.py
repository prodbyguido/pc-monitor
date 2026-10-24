"""Draw PC Monitor's app icon (assets/icon.ico): a dark panel with a green live graph.

Run once with:  .venv\\Scripts\\python.exe make_icon.py
"""
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen

app = QGuiApplication([])
size = 256
img = QImage(size, size, QImage.Format_ARGB32)
img.fill(Qt.transparent)
p = QPainter(img)
p.setRenderHint(QPainter.Antialiasing)
p.setPen(QPen(QColor("#45526b"), 10))
p.setBrush(QColor("#1c2331"))
p.drawRoundedRect(QRectF(8, 8, size - 16, size - 16), 48, 48)
points = [(40, 170), (78, 150), (104, 176), (138, 96), (166, 132), (190, 70), (216, 104)]
line = QPainterPath(QPointF(*points[0]))
for x, y in points[1:]:
    line.lineTo(x, y)
fill = QPainterPath(line)
fill.lineTo(216, 216)
fill.lineTo(40, 216)
fill.closeSubpath()
p.setPen(Qt.NoPen)
p.setBrush(QColor(61, 214, 140, 70))
p.drawPath(fill)
p.setPen(QPen(QColor("#3dd68c"), 16, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
p.setBrush(Qt.NoBrush)
p.drawPath(line)
p.end()
out = Path(__file__).resolve().parent / "assets"
out.mkdir(exist_ok=True)
img.save(str(out / "icon.ico"))
img.save(str(out / "icon.png"))
print("saved", out / "icon.ico")
