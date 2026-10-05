"""Small shared widgets: mono labels, themed buttons, the frameless card dialog."""

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QWidget

from kapture import icons
from kapture.theme import BTN_H, card_css, mono, theme


def label(text: str, t: dict, color: str = "text_sec", px: int = 11) -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(mono(px))
    lbl.setStyleSheet(f"color: {t[color]};")
    return lbl


def button(text: str, css: str, t: dict = None, icon: str = None,
           tip: str = None, square: bool = False) -> QPushButton:
    """Mono-label button, optionally with a leading icon; `square` = icon-only."""
    b = QPushButton("" if square else text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFont(mono(12))
    b.setStyleSheet(css)
    if icon:
        b.setIcon(icons.icon(icon, t))
        b.setIconSize(QSize(18, 18))
    if square:
        b.setFixedSize(BTN_H, BTN_H)
    b.setToolTip(tip or (text if square else ""))
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus if square else Qt.FocusPolicy.TabFocus)
    return b


class CardDialog(QDialog):
    """Frameless, draggable, Esc-to-close dialog drawn as one flat card.
    Subclasses fill `self.body` (a QVBoxLayout) in build()."""

    def __init__(self, width: int, parent=None):
        super().__init__(parent)
        self.t = theme()
        self._drag = QPoint()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QWidget()
        card.setObjectName("card")
        card.setFixedWidth(width)
        card.setStyleSheet(card_css(self.t))
        outer.addWidget(card)
        self.body = QVBoxLayout(card)
        self.body.setContentsMargins(32, 32, 32, 24)
        self.body.setSpacing(0)
        self.build()

    def build(self):
        raise NotImplementedError

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)
