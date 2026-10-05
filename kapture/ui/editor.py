"""Floating annotation editor shown after a region capture.

Tools: pen, highlighter, arrow, box, text, numbered steps, pixelate.
Draggable from the toolbar; remembers its last position across captures.
"""

import asyncio
import logging
import math
import os
import time

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication, QIcon, QKeySequence,
                           QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QShortcut)
from PySide6.QtWidgets import (QApplication, QColorDialog, QFileDialog, QHBoxLayout, QLabel,
                               QLineEdit, QMenu, QPushButton, QVBoxLayout, QWidget)

from shiboken6 import isValid

from kapture import icons, spawn
from kapture.config import CONFIG, save_config, save_dir
from kapture.theme import BTN_H, DARK, btn_css, menu_css, mono, primary_css, sans, theme
from kapture.ui.pin import PinWindow
from kapture.ui.widgets import button

log = logging.getLogger("kapture")

TOOLS = [
    # tool      icon             key  tooltip
    ("pen",    "pencil",        "P", "Pen"),
    ("marker", "highlighter",   "H", "Highlighter"),
    ("arrow",  "move-up-right", "A", "Arrow"),
    ("rect",   "square",        "R", "Box"),
    ("text",   "type",          "T", "Text"),
    ("step",   "hash",          "N", "Numbered step"),
    ("blur",   "grid-3x3",      "B", "Pixelate — hide sensitive info"),
]
STROKES = [("S", 2), ("M", 4), ("L", 8)]          # label, pen width in px
TEXT_PX = (18, 24, 36)                            # text size per stroke step
COLORS = [("Red", "#E63232"), ("Amber", "#F2A516"), ("Green", "#2FA84F"),
          ("Blue", "#2F6FED"), ("White", "#FFFFFF"), ("Black", "#111111")]
CARD_RADIUS = 12
UNDO_DEPTH = 40
UNDO_BUDGET = 384 * 1024 * 1024       # bytes of overlay snapshots kept for undo
MARKER_OPACITY = 0.43


def _swatch(color: str, size: int = 14) -> QIcon:
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(DARK["border"]), 2))
    p.setBrush(QColor(color))
    p.drawEllipse(2, 2, size * 2 - 4, size * 2 - 4)
    p.end()
    return QIcon(pm)


class _Canvas(QWidget):
    """Shows base + annotation layers at the editor's view scale; forwards mouse
    input in IMAGE coordinates."""

    def __init__(self, ed: 'AnnotationWindow'):
        super().__init__()
        self.ed = ed
        self.setFixedSize(ed.view_size)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _img(self, e) -> QPoint:
        return (e.position() / self.ed.scale).toPoint()

    def paintEvent(self, event):
        p = QPainter(self)
        p.drawPixmap(0, 0, self.ed.view())     # cached composite; Qt clips to the dirty rect
        if self.ed.drawing:
            p.setRenderHints(QPainter.RenderHint.Antialiasing |
                             QPainter.RenderHint.SmoothPixmapTransform)
            p.scale(self.ed.scale, self.ed.scale)
            self.ed.paint_live(p)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.ed.press(self._img(e))

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self.ed.drag_to(self._img(e))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.ed.release(self._img(e))


class _DragBar(QWidget):
    """Toolbar strip that doubles as the window drag handle."""

    def __init__(self, ed: 'AnnotationWindow'):
        super().__init__()
        self.ed = ed
        self._offset = None

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._offset = e.globalPosition().toPoint() - self.ed.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._offset is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.ed.move(e.globalPosition().toPoint() - self._offset)

    def mouseReleaseEvent(self, e):
        if self._offset is not None:
            self._offset = None
            AnnotationWindow.last_pos = self.ed.frameGeometry().topLeft()


class _TextEdit(QLineEdit):
    """Inline text entry. Keeps Esc for itself (cancel) instead of letting the
    window's Esc shortcut close the whole editor."""

    def __init__(self, ed: 'AnnotationWindow'):
        super().__init__(ed.canvas)
        self.ed = ed

    def event(self, e):
        if e.type() == QEvent.Type.ShortcutOverride and e.key() == Qt.Key.Key_Escape:
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.ed.cancel_text()
        else:
            super().keyPressEvent(e)


class AnnotationWindow(QWidget):
    last_pos: QPoint = None
    _open: set = set()          # open editors keep themselves alive (no parent)

    def __init__(self, pixmap: QPixmap, region: QRect):
        """`region` is the selection in desktop coordinates."""
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        AnnotationWindow._open.add(self)
        self.t = theme()
        self.base = pixmap                                  # devicePixelRatio stamped
        self.region = region
        self.overlay = QPixmap(pixmap.size())
        self.overlay.setDevicePixelRatio(pixmap.devicePixelRatio())
        self.overlay.fill(Qt.GlobalColor.transparent)

        self.tool = "pen"
        self.color = QColor(CONFIG.get("color") or COLORS[0][1])
        if not self.color.isValid():                        # hand-edited config
            self.color = QColor(COLORS[0][1])
        self.stroke = CONFIG.get("stroke") if CONFIG.get("stroke") in (0, 1, 2) else 1
        self.step = 1
        self.drawing = False
        self.start = self.cur = QPoint()
        self.points: list = []
        self._undo: list = []                               # (overlay, step) snapshots
        self._redo: list = []
        self._text: '_TextEdit | None' = None
        self._text_pos = QPoint()
        self._tool_btns: dict = {}
        self._view: 'QPixmap | None' = None                # cached base+overlay at view scale
        self.live: 'QPixmap | None' = None                 # in-progress pen/marker stroke
        # Snapshots are full overlay copies: cap their total memory, not just count.
        snap = max(1, pixmap.width() * pixmap.height() * 4)
        self.undo_depth = max(5, min(UNDO_DEPTH, UNDO_BUDGET // snap))

        # Fit very large captures on screen: the view scales, the image doesn't.
        self.logical = pixmap.deviceIndependentSize().toSize()
        # The monitor the selection was made on — not always the primary one.
        self.screen_geo = (QGuiApplication.screenAt(region.center())
                           or QGuiApplication.primaryScreen()).availableGeometry()
        avail = self.screen_geo
        self.scale = min(1.0, avail.width() * 0.95 / max(1, self.logical.width()),
                         (avail.height() * 0.95 - BTN_H - 40) / max(1, self.logical.height()))
        self.view_size = QSize(round(self.logical.width() * self.scale),
                               round(self.logical.height() * self.scale))

        self._build()
        self._shortcuts()
        self._place()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build(self):
        t = self.t
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # Card: flat, no shadow; a 2px secondary-ink frame keeps the screenshot
        # from blending into the desktop behind it.
        self.card = QWidget()
        self.card.setObjectName("card")
        self.card.setStyleSheet(f"QWidget#card {{ background: {t['bg']};"
                                f" border: 2px solid {t['text_sec']}; border-radius: {CARD_RADIUS}px; }}")
        outer.addWidget(self.card)
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(0)
        self.canvas = _Canvas(self)
        lay.addWidget(self.canvas, 0, Qt.AlignmentFlag.AlignHCenter)

        # Close: circular, flat; red only on hover — the destructive interrupt.
        self.close_btn = QPushButton(self.card)
        self.close_btn.setIcon(icons.icon("x", t, 14))
        self.close_btn.setFixedSize(28, 28)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_btn.setToolTip("Close  ·  Esc")
        self.close_btn.setStyleSheet(f"""
            QPushButton {{ background: {t['bg']}; border: 1px solid {t['border']}; border-radius: 14px; }}
            QPushButton:hover {{ background: {t['red']}; border-color: {t['red']}; }}
        """)
        self.close_btn.clicked.connect(self.close)

        bar = _DragBar(self)
        bar.setObjectName("toolbar")
        bar.setStyleSheet(f"QWidget#toolbar {{ background: {t['bg']};"
                          f" border-bottom-left-radius: {CARD_RADIUS - 1}px;"
                          f" border-bottom-right-radius: {CARD_RADIUS - 1}px; }}")
        tb = QHBoxLayout(bar)
        tb.setContentsMargins(16, 12, 16, 12)
        tb.setSpacing(6)
        square = btn_css(t, 6, 0)

        # Drawing tools — icon-only, technical corners; the active one inverts.
        for tool, ic, key, tip in TOOLS:
            b = button(tip, square, t, ic, tip=f"{tip}  ·  {key}", square=True)
            b.setCheckable(True)
            b.setChecked(tool == self.tool)
            b.clicked.connect(lambda _=False, tl=tool: self.select_tool(tl))
            self._tool_btns[tool] = b
            tb.addWidget(b)
        tb.addSpacing(12)               # spacing, not divider lines, separates groups

        # Stroke width (cycles S → M → L) and colour (quick swatches + custom).
        self.stroke_btn = button(STROKES[self.stroke][0], square)
        self.stroke_btn.setFixedSize(BTN_H, BTN_H)
        self.stroke_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stroke_btn.setToolTip("Stroke & text size  ·  [ ]")
        self.stroke_btn.clicked.connect(lambda: self.set_stroke(self.stroke + 1))
        tb.addWidget(self.stroke_btn)

        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(BTN_H, BTN_H)
        self.color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.color_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.color_btn.setToolTip("Colour")
        menu = QMenu(self)
        menu.setStyleSheet(menu_css(t))
        for name, hexv in COLORS:
            menu.addAction(_swatch(hexv), name, lambda h=hexv: self.set_color(QColor(h)))
        menu.addSeparator()
        menu.addAction(icons.icon("palette", t, menu=True), "Custom…", self._pick_color)
        self.color_btn.setMenu(menu)
        self._refresh_color_btn()
        tb.addWidget(self.color_btn)
        tb.addSpacing(12)

        for ic, tip, slot in (("undo-2", "Undo  ·  Ctrl+Z", self.undo),
                              ("redo-2", "Redo  ·  Ctrl+Shift+Z", self.redo)):
            b = button(tip, square, t, ic, tip=tip, square=True)
            b.clicked.connect(slot)
            tb.addWidget(b)

        # Inline status ("[COPIED]") — takes the slack space; no toast popups.
        self.status = QLabel()
        self.status.setFont(mono(11))
        self.status.setStyleSheet(f"color: {t['text_sec']}; background: transparent;")
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.status.setMinimumWidth(self.status.fontMetrics().horizontalAdvance("[NO REDO]") + 8)
        tb.addWidget(self.status, 1)
        tb.addSpacing(6)

        pin = button("Pin", square, t, "pin", tip="Pin to screen  ·  Ctrl+P", square=True)
        pin.clicked.connect(self.pin)
        tb.addWidget(pin)
        copy = button("Copy", square, t, "copy", tip="Copy  ·  Ctrl+C", square=True)
        copy.clicked.connect(self.copy)
        tb.addWidget(copy)
        save = button("Save", primary_css(t), {**t, "text": t["on_accent"]}, "download",
                      tip="Save  ·  Ctrl+S")
        save.clicked.connect(lambda: spawn(self.save()))
        tb.addWidget(save)

        lay.addWidget(bar)
        self.adjustSize()

    def _shortcuts(self):
        keys = [("Ctrl+C", self.copy), ("Ctrl+S", lambda: spawn(self.save())),
                ("Ctrl+P", self.pin), ("Ctrl+Z", self.undo), ("Ctrl+Shift+Z", self.redo),
                ("Ctrl+Y", self.redo), ("Esc", self.close),
                ("[", lambda: self.set_stroke(self.stroke - 1)),
                ("]", lambda: self.set_stroke(self.stroke + 1))]
        keys += [(key, lambda tl=tool: self.select_tool(tl)) for tool, _i, key, _t in TOOLS]
        for seq, slot in keys:
            QShortcut(QKeySequence(seq), self, activated=slot)

    def _place(self):
        last = AnnotationWindow.last_pos
        if last is not None and QGuiApplication.screenAt(last) is not None:
            self.move(last)                 # where the user last dragged it (if still on a screen)
        else:
            g = self.screen_geo
            self.move(max(g.left(), min(self.region.x(), g.right() - self.width())),
                      max(g.top(), min(self.region.y(), g.bottom() - self.height())))
        self.show()
        self.raise_()
        self.activateWindow()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.close_btn.move(self.card.width() - self.close_btn.width() - 10, 10)
        self.close_btn.raise_()

    def flash(self, text: str):
        """Bracketed inline status text in the toolbar, e.g. [SAVED]."""
        msg = f"[{text}]"
        self.status.setText(msg)
        # status as context: the timer dies with the window if it closes first
        QTimer.singleShot(1600, self.status,
                          lambda: self.status.text() == msg and self.status.clear())

    # ── tool state ───────────────────────────────────────────────────────────

    def select_tool(self, tool: str):
        self.commit_text()
        self.tool = tool
        for tl, b in self._tool_btns.items():
            b.setChecked(tl == tool)
        self.canvas.setCursor(Qt.CursorShape.IBeamCursor if tool == "text"
                              else Qt.CursorShape.CrossCursor)

    def set_stroke(self, i: int):
        self.stroke = i % len(STROKES)
        self.stroke_btn.setText(STROKES[self.stroke][0])
        CONFIG["stroke"] = self.stroke
        save_config(CONFIG)

    def set_color(self, color: QColor):
        self.color = color
        self._refresh_color_btn()
        CONFIG["color"] = color.name()
        save_config(CONFIG)

    def _pick_color(self):
        col = QColorDialog.getColor(self.color, self, "Annotation colour")
        if col.isValid():
            self.set_color(col)

    def _refresh_color_btn(self):
        self.color_btn.setStyleSheet(f"""
            QPushButton {{ background: {self.color.name()}; border: 1px solid {self.t['border']};
                           border-radius: {BTN_H // 2}px; }}
            QPushButton:hover {{ border-color: {self.t['text']}; }}
            QPushButton::menu-indicator {{ image: none; width: 0; }}
        """)

    def _width(self) -> int:
        return STROKES[self.stroke][1]

    def _pen(self, color: QColor = None, width: float = None) -> QPen:
        return QPen(color or self.color, width or self._width(), Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)

    # ── input (image coordinates) ────────────────────────────────────────────

    def press(self, pt: QPoint):
        self.commit_text()
        if self.tool == "text":
            self._open_text(pt)
            return
        self.drawing = True
        self.start = self.cur = pt
        self.points = [pt]
        if self.tool in ("pen", "marker"):
            self.live = QPixmap(self.overlay.size())
            self.live.setDevicePixelRatio(self.overlay.devicePixelRatio())
            self.live.fill(Qt.GlobalColor.transparent)
        self._repaint(self._live_bounds())

    def drag_to(self, pt: QPoint):
        if not self.drawing or pt == self.cur:
            return
        before = self._live_bounds()
        last, self.cur = self.cur, pt
        if self.tool in ("pen", "marker"):
            # Draw just the new segment: frame cost stays flat however long the
            # stroke gets. Round caps make the joints seamless.
            self.points.append(pt)
            p = QPainter(self.live)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(self._pen(self.color, self._stroke_px()))
            p.drawLine(last, pt)
            p.end()
            pad = self._stroke_px()
            self._repaint(QRectF(QRect(last, pt).normalized()).adjusted(-pad, -pad, pad, pad))
        else:
            self._repaint(before.united(self._live_bounds()))

    def _stroke_px(self) -> int:
        return self._width() * 3 + 8 if self.tool == "marker" else self._width()

    def _live_bounds(self) -> QRectF:
        """Image-space area the in-progress shape covers (for partial repaints)."""
        w = self._width()
        if self.tool == "step":
            r = 10 + w * 2 + 2
            return QRectF(self.cur.x() - r, self.cur.y() - r, 2 * r, 2 * r)
        pad = max(12, w * 4) + w if self.tool == "arrow" else w + 3
        return QRectF(QRect(self.start, self.cur).normalized()).adjusted(-pad, -pad, pad, pad)

    def _repaint(self, image_rect: QRectF):
        s = self.scale
        self.canvas.update(QRectF(image_rect.x() * s, image_rect.y() * s, image_rect.width() * s,
                                  image_rect.height() * s).toAlignedRect().adjusted(-2, -2, 2, 2))

    def view(self) -> QPixmap:
        """Base + annotations, scaled to the canvas with rounded top corners —
        rebuilt only when the annotations change, so a frame is one blit."""
        if self._view is None:
            d = self.canvas.devicePixelRatioF()
            pm = QPixmap(round(self.view_size.width() * d), round(self.view_size.height() * d))
            pm.setDevicePixelRatio(d)
            pm.fill(Qt.GlobalColor.transparent)
            r, rad = QRectF(QPointF(0, 0), self.view_size.toSizeF()), CARD_RADIUS - 2
            path = QPainterPath()
            path.moveTo(r.left(), r.bottom())
            path.lineTo(r.left(), r.top() + rad)
            path.quadTo(r.left(), r.top(), r.left() + rad, r.top())
            path.lineTo(r.right() - rad, r.top())
            path.quadTo(r.right(), r.top(), r.right(), r.top() + rad)
            path.lineTo(r.right(), r.bottom())
            p = QPainter(pm)
            p.setRenderHints(QPainter.RenderHint.Antialiasing |
                             QPainter.RenderHint.SmoothPixmapTransform)
            p.setClipPath(path)
            p.scale(self.scale, self.scale)
            p.drawPixmap(0, 0, self.base)
            p.drawPixmap(0, 0, self.overlay)
            p.end()
            self._view = pm
        return self._view

    def _changed(self):
        self._view = None
        self.canvas.update()

    def release(self, pt: QPoint):
        if not self.drawing:
            return
        self.drawing = False
        self.cur = pt
        self._commit_shape()
        self.live = None
        self._changed()

    # ── painting ─────────────────────────────────────────────────────────────

    def paint_live(self, p: QPainter):
        if self.tool == "blur":
            # Preview the redaction area (the mosaic is applied on release).
            r = QRect(self.start, self.cur).normalized()
            scrim = QColor(DARK["bg"])
            scrim.setAlpha(95)
            p.fillRect(r, scrim)
            p.setPen(QPen(QColor(DARK["accent"]), 1, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r)
        else:
            self._draw(p)

    def _draw(self, p: QPainter):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self.tool in ("pen", "marker"):
            if self.live is not None:
                # The highlighter's opaque layer is shown/committed translucent as
                # a whole, so overlapping segments never darken.
                p.setOpacity(MARKER_OPACITY if self.tool == "marker" else 1.0)
                p.drawPixmap(0, 0, self.live)
                p.setOpacity(1.0)
        elif self.tool == "arrow":
            self._arrow(p, QPointF(self.start), QPointF(self.cur))
        elif self.tool == "rect":
            p.setPen(self._pen(None, self._width()))
            p.drawRect(QRect(self.start, self.cur).normalized())
        elif self.tool == "step":
            self._badge(p, QPointF(self.cur), self.step)

    def _arrow(self, p: QPainter, a: QPointF, b: QPointF):
        dx, dy = b.x() - a.x(), b.y() - a.y()
        if math.hypot(dx, dy) < 2:
            return
        ang = math.atan2(dy, dx)
        head = max(12, self._width() * 4)
        # Stop the shaft inside the head so the round cap never pokes past the tip.
        neck = QPointF(b.x() - head * 0.7 * math.cos(ang), b.y() - head * 0.7 * math.sin(ang))
        p.setPen(self._pen())
        p.drawLine(a, neck)
        wing = math.pi / 7
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.color)
        p.drawPolygon(QPolygonF([
            b,
            QPointF(b.x() - head * math.cos(ang - wing), b.y() - head * math.sin(ang - wing)),
            QPointF(b.x() - head * math.cos(ang + wing), b.y() - head * math.sin(ang + wing))]))

    def _badge(self, p: QPainter, c: QPointF, n: int):
        r = 10 + self._width() * 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.color)
        p.drawEllipse(c, r, r)
        p.setPen(QColor("#111111") if self.color.lightness() > 170 else QColor("#FFFFFF"))
        p.setFont(sans(round(r * 1.1), medium=True))
        p.drawText(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), Qt.AlignmentFlag.AlignCenter, str(n))

    def _commit_shape(self):
        if self.tool in ("pen", "marker") and len(self.points) < 2:
            return
        self._push_undo()
        if self.tool == "blur":
            self._pixelate(QRect(self.start, self.cur).normalized())
            return
        p = QPainter(self.overlay)
        self._draw(p)
        p.end()
        if self.tool == "step":
            self.step += 1

    def _pixelate(self, rect: QRect):
        """Redact by mosaic (downscale → nearest-neighbour upscale), baked onto the
        overlay — unlike a blur it can't be reversed, which is the point."""
        rect = rect.intersected(QRect(QPoint(0, 0), self.logical))
        if rect.width() < 4 or rect.height() < 4:
            return
        d = self.base.devicePixelRatio()
        dev = QRect(round(rect.x() * d), round(rect.y() * d),
                    round(rect.width() * d), round(rect.height() * d))
        cells = self.final().copy(dev).scaled(max(1, rect.width() // 10), max(1, rect.height() // 10),
                                              Qt.AspectRatioMode.IgnoreAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation)
        cells.setDevicePixelRatio(1.0)
        p = QPainter(self.overlay)          # no smoothing hint → hard mosaic edges
        p.drawPixmap(QRectF(rect), cells, QRectF(cells.rect()))
        p.end()

    # ── text tool ────────────────────────────────────────────────────────────

    def _text_font(self, scale: float = 1.0) -> QFont:
        return sans(round(TEXT_PX[self.stroke] * scale), medium=True)

    def _open_text(self, pt: QPoint):
        fm = QFontMetrics(self._text_font())
        self._text_pos = QPoint(pt.x(), pt.y() - fm.height() // 2)
        ed = self._text = _TextEdit(self)
        ed.setFont(self._text_font(self.scale))
        ed.setTextMargins(0, 0, 0, 0)
        ed.setStyleSheet(f"QLineEdit {{ background: transparent; color: {self.color.name()};"
                         f" border: 1px dashed {DARK['accent']}; padding: 0; }}")
        vfm = ed.fontMetrics()
        ed.setFixedSize(48, vfm.height() + 4)
        ed.textChanged.connect(
            lambda s: ed.setFixedWidth(max(48, vfm.horizontalAdvance(s) + 24)))
        ed.editingFinished.connect(self.commit_text)
        ed.move((QPointF(self._text_pos) * self.scale).toPoint() - QPoint(1, 2))
        ed.show()
        ed.setFocus()

    def commit_text(self):
        ed, self._text = self._text, None
        if ed is None:
            return
        text = ed.text().strip()
        ed.hide()
        ed.deleteLater()
        self.setFocus()
        if not text:
            return
        self._push_undo()
        p = QPainter(self.overlay)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        font = self._text_font()
        p.setFont(font)
        p.setPen(self.color)
        p.drawText(self._text_pos + QPoint(0, QFontMetrics(font).ascent()), text)
        p.end()
        self._changed()

    def cancel_text(self):
        ed, self._text = self._text, None
        if ed is not None:
            ed.hide()
            ed.deleteLater()
            self.setFocus()

    # ── history ──────────────────────────────────────────────────────────────

    def _push_undo(self):
        # QPixmap copies are implicitly shared — cheap until the next paint.
        self._undo.append((QPixmap(self.overlay), self.step))
        del self._undo[:-self.undo_depth]
        self._redo.clear()

    def undo(self):
        if not self._undo:
            return self.flash("No undo")
        self._redo.append((QPixmap(self.overlay), self.step))
        self.overlay, self.step = self._undo.pop()
        self._changed()

    def redo(self):
        if not self._redo:
            return self.flash("No redo")
        self._undo.append((QPixmap(self.overlay), self.step))
        self.overlay, self.step = self._redo.pop()
        self._changed()

    # ── output ───────────────────────────────────────────────────────────────

    def final(self) -> QPixmap:
        result = QPixmap(self.base)
        p = QPainter(result)
        p.drawPixmap(0, 0, self.overlay)
        p.end()
        return result

    def copy(self):
        self.commit_text()
        QApplication.clipboard().setPixmap(self.final())
        self.flash("Copied")

    def pin(self):
        self.commit_text()
        PinWindow(self.final(), self.canvas.mapToGlobal(QPoint(0, 0)), self.scale)
        self.close()

    async def save(self):
        self.commit_text()
        image = self.final().toImage()          # QImage: safe off the GUI thread
        name = f"kapture_{time.strftime('%Y%m%d_%H%M%S')}.png"
        if CONFIG.get("auto_save"):
            path = os.path.join(save_dir(), name)
        else:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Screenshot", os.path.join(save_dir(), name),
                "PNG Image (*.png);;JPEG Image (*.jpg);;All Files (*)")
            if not path:
                return
        ok = await asyncio.to_thread(image.save, path)
        log.info("editor: saved %s (%s)", path, ok)
        if isValid(self):                       # may have been closed while saving
            self.flash("Saved" if ok else "Save failed")

    def closeEvent(self, e):
        self.cancel_text()
        AnnotationWindow._open.discard(self)
        self._undo.clear()                      # release layers/snapshots right away
        self._redo.clear()
        self._view = self.live = None
        super().closeEvent(e)
