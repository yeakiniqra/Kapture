"""Pin a screenshot to the screen as a floating, always-on-top reference."""

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import QApplication, QWidget

from kapture.theme import theme


class PinWindow(QWidget):
    """Drag to move · scroll to zoom · Ctrl+C to copy · double-click / Esc to close."""

    _open: set = set()          # keep Python refs alive while pins are on screen

    def __init__(self, pixmap: QPixmap, pos: QPoint, zoom: float = 1.0):
        super().__init__(None, Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.pm = pixmap
        self.zoom = zoom
        self.border = QColor(theme()["text_sec"])
        self._drag = QPoint()
        self.setToolTip("Pinned — drag to move · scroll to zoom · double-click to close")
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.close)
        QShortcut(QKeySequence.StandardKey.Copy, self,
                  activated=lambda: QApplication.clipboard().setPixmap(self.pm))
        self._fit()
        self.move(pos)
        self.show()
        PinWindow._open.add(self)

    def _fit(self):
        size = self.pm.deviceIndependentSize() * self.zoom
        self.setFixedSize(QSize(round(size.width()), round(size.height())))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(self.rect(), self.pm)
        p.setPen(QPen(self.border, 1))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))

    def wheelEvent(self, e):
        self.zoom = min(4.0, max(0.2, self.zoom * (1.1 if e.angleDelta().y() > 0 else 1 / 1.1)))
        self._fit()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseDoubleClickEvent(self, e):
        self.close()

    def closeEvent(self, e):
        PinWindow._open.discard(self)
        super().closeEvent(e)
