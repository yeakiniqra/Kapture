#!/usr/bin/env python3
"""Render assets/logo.svg into the raster icons the packages need.

    venv/bin/python scripts/make_icons.py

  assets/app-logo.png  512px  — Linux launcher / About dialog / website
  assets/app.ico       16–256 — Windows executable and taskbar
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

app = QGuiApplication(sys.argv)
svg = QSvgRenderer(os.path.join(ROOT, "assets", "logo.svg"))


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    svg.render(p, QRectF(0, 0, size, size))
    p.end()
    return img


render(512).save(os.path.join(ROOT, "assets", "app-logo.png"))

# .ico via Pillow (a build-time dependency only; the app itself never needs it).
from PIL import Image  # noqa: E402

frames = []
for size in (16, 24, 32, 48, 64, 128, 256):
    img = render(size).convertToFormat(QImage.Format.Format_RGBA8888)
    frames.append(Image.frombytes("RGBA", (size, size), bytes(img.constBits())))
frames[-1].save(os.path.join(ROOT, "assets", "app.ico"), format="ICO",
                append_images=frames[:-1], sizes=[(f.width, f.height) for f in frames])
print("wrote assets/app-logo.png and assets/app.ico")
