"""Monoline Lucide icons (assets/icons, ISC), tinted to theme tokens at load time."""

import os
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from kapture.config import CACHE_DIR, resource_path


@lru_cache(maxsize=None)
def _svg(name: str) -> str:
    with open(resource_path(os.path.join("assets", "icons", f"{name}.svg"))) as f:
        return f.read()


@lru_cache(maxsize=None)
def pixmap(name: str, color: str, size: int = 18, stroke: float = 1.5) -> QPixmap:
    """Render icon `name` in `color` at `size` logical px (crisp on HiDPI)."""
    svg = (_svg(name).replace("currentColor", color)
                     .replace('stroke-width="2"', f'stroke-width="{stroke}"'))
    dpr = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1.0
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    QSvgRenderer(QByteArray(svg.encode())).render(p, QRectF(0, 0, pm.width(), pm.height()))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def file(name: str, color: str, size: int = 16) -> str:
    """Tinted icon as a cached PNG path — for QSS `image: url(...)`, which can't
    take a QPixmap. Rendered at 2x so it stays crisp when scaled down."""
    path = os.path.join(CACHE_DIR, "icons", f"{name}-{color.lstrip('#')}-{size}.png")
    if not os.path.isfile(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        pixmap(name, color, size * 2).save(path)    # save() ignores devicePixelRatio
    return path


def icon(name: str, t: dict, size: int = 18, menu: bool = False) -> QIcon:
    """Theme icon: text colour normally, inverted (bg colour) when a button is
    checked — or, for menus, when the item is highlighted (Qt draws a highlighted
    menu item in Active mode; buttons use Active for focus, so only menus get it)."""
    ic = QIcon()
    ic.addPixmap(pixmap(name, t["text"], size), QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(pixmap(name, t["bg"], size), QIcon.Mode.Normal, QIcon.State.On)
    if menu:
        ic.addPixmap(pixmap(name, t["bg"], size), QIcon.Mode.Active, QIcon.State.Off)
    else:
        ic.addPixmap(pixmap(name, t["text"], size), QIcon.Mode.Active, QIcon.State.Off)
        ic.addPixmap(pixmap(name, t["bg"], size), QIcon.Mode.Active, QIcon.State.On)
    return ic
