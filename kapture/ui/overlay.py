"""Fullscreen region picker drawn over the frozen screen grab."""

import logging

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from kapture import windows
from kapture.capture import CaptureResult
from kapture.config import WINDOWS
from kapture.theme import DARK, mono

log = logging.getLogger("kapture")


class OverlayWindow(QWidget):
    """Drag to select (auto-copied), Enter = whole screen, Esc / right-click = cancel."""

    region_selected = Signal(QPixmap, QRect)
    fullscreen_selected = Signal(QPixmap)

    def __init__(self, result: CaptureResult):
        super().__init__()
        self.result = result
        self.origin = QPoint()
        self.selection = QRect()
        self.is_drawing = False
        self._dim = None
        self._font = mono(13)
        self._fm = QFontMetrics(self._font)
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        # Cover all screens using LOGICAL virtual geometry (the captured pixmap
        # carries devicePixelRatio, so logical coords map 1:1 when painted).
        if WINDOWS:
            # showFullScreen() would clamp to one monitor; a plain show() at the
            # virtual geometry spans them all. Tool keeps it off the taskbar.
            self.setWindowFlags(flags | Qt.WindowType.Tool)
            self.setGeometry(result.geometry)
            self.show()
        else:
            self.setWindowFlags(flags | Qt.WindowType.BypassWindowManagerHint)
            self.setGeometry(result.geometry)
            self.showFullScreen()
        self.activateWindow()
        self.setFocus()
        windows.bring_to_front(self)

    def _scrimmed(self) -> QPixmap:
        """The frozen screen with the scrim baked in — built once, so a drag frame
        is a clipped blit instead of a full-screen alpha blend."""
        dim = QPixmap(self.result.pixmap)
        scrim = QColor(DARK["bg"])          # dark ink whatever the theme: it sits on
        scrim.setAlpha(150)                 # arbitrary screen content, not app chrome
        p = QPainter(dim)
        p.fillRect(dim.rect(), scrim)
        p.end()
        return dim

    def _label_box(self, sel: QRect) -> QRect:
        """Size readout box, above the selection (below it at the screen's top)."""
        box = QRect(0, 0, self._fm.horizontalAdvance(f"{sel.width()} × {sel.height()}") + 16,
                    self._fm.height() + 8)
        box.moveBottomLeft(QPoint(sel.x(), sel.y() - 6))
        if box.top() < 0:
            box.moveTopLeft(QPoint(sel.x(), sel.bottom() + 6))
        return box

    def _dirty(self) -> QRect:
        """Everything the current selection paints: frame, handles, label."""
        if not self.is_drawing or self.selection.isNull():
            return QRect()
        sel = self.selection.normalized()
        return sel.adjusted(-4, -4, 4, 4).united(self._label_box(sel).adjusted(-1, -1, 1, 1))

    def paintEvent(self, event):
        p = QPainter(self)
        if self._dim is None:
            self._dim = self._scrimmed()
        p.drawPixmap(0, 0, self._dim)       # clipped to the dirty region by Qt
        ink, accent = QColor(DARK["bg"]), QColor(DARK["accent"])
        ink.setAlpha(215)
        p.setFont(self._font)

        if self.is_drawing and not self.selection.isNull():
            sel = self.selection.normalized()
            # Bright cut-out. drawPixmap's SOURCE rect is in device pixels, so
            # scale the logical selection by dpr (a 1:1 rect breaks on HiDPI).
            d = self.result.dpr
            p.drawPixmap(QRectF(sel), self.result.pixmap,
                         QRectF(sel.x() * d, sel.y() * d, sel.width() * d, sel.height() * d))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(accent, 2))
            p.drawRect(sel)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(accent))
            for pt in (sel.topLeft(), sel.topRight(), sel.bottomLeft(), sel.bottomRight()):
                p.drawRect(pt.x() - 3, pt.y() - 3, 6, 6)
            box = self._label_box(sel)
            p.fillRect(box, ink)
            p.setPen(QColor(DARK["text"]))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, f"{sel.width()} × {sel.height()}")
        elif self.selection.isNull():
            hint = "Drag to select      Enter full screen      Esc cancel"
            box = QRect(0, 0, self._fm.horizontalAdvance(hint) + 48, self._fm.height() + 24)
            box.moveCenter(self.rect().center())
            p.fillRect(box, ink)
            p.setPen(QColor(DARK["text"]))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, hint)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.close()
        elif event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.position().toPoint()
            self.selection = QRect(self.origin, QSize(0, 0))
            self.is_drawing = True
            self.update()                   # full: the centred hint disappears

    def mouseMoveEvent(self, event):
        if self.is_drawing:
            # Repaint only what the old and new selection cover.
            before = self._dirty()
            self.selection = QRect(self.origin, event.position().toPoint())
            self.update(before.united(self._dirty()))

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.is_drawing:
            return
        self.is_drawing = False
        sel = self.selection.normalized()
        if sel.width() <= 5 or sel.height() <= 5:
            self.close()
            return
        # DPR-correct crop: logical selection scaled up inside crop_logical().
        cropped = self.result.crop_logical(sel)
        origin = self.result.geometry.topLeft()     # overlay-local → desktop coords
        self.close()
        QApplication.clipboard().setPixmap(cropped)
        log.info("overlay: region %dx%d at (%d,%d), copied", sel.width(), sel.height(),
                 sel.x(), sel.y())
        self.region_selected.emit(cropped, sel.translated(origin))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_F):
            pixmap = self.result.pixmap
            self.close()
            self.fullscreen_selected.emit(pixmap)

    def closeEvent(self, event):
        # Drop the full-desktop pixmaps now (tens of MB at 4K) rather than
        # whenever the next capture replaces this window.
        self._dim = None
        self.result = None
        super().closeEvent(event)
