#!/usr/bin/env python3
"""
Kapture - A Lightshot-like screenshot tool for Ubuntu
Features: region selection, annotation toolbar, copy & save
"""

import sys
import os
import time
import math
import shutil
import asyncio
import logging
import tempfile
import subprocess
from urllib.parse import urlparse, unquote
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QHBoxLayout, QVBoxLayout,
    QSystemTrayIcon, QMenu, QAction, QMessageBox, QFileDialog,
    QColorDialog, QRubberBand, QSizePolicy, QDialog,
    QFrame, QShortcut, QGraphicsDropShadowEffect, QComboBox, QCheckBox
)
from PyQt5.QtGui import (
    QPixmap, QColor, QPainter, QPen, QBrush, QFont,
    QCursor, QIcon, QGuiApplication, QScreen, QClipboard, QPolygon,
    QPainterPath, QKeySequence, QDesktopServices
)
from PyQt5.QtCore import (
    Qt, QRect, QRectF, QPoint, QPointF, QSize, QTimer, QUrl,
    pyqtSignal, pyqtSlot, QObject
)
from PyQt5.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
from PyQt5.QtNetwork import QLocalServer, QLocalSocket
from pynput import keyboard
import qasync


# ─── LOGGING ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("kapture")


# ─── CONFIG ───────────────────────────────────────────────────────────────────

HOTKEYS = ["<print_screen>", "<ctrl>+<shift>+s"]   # All active hotkeys
APP_NAME = "Kapture"
VERSION  = "3.0.2"
AUTHOR   = "Yeakin Iqra"

# Per-user single-instance / remote-trigger socket name.
_IPC_NAME = f"kapture-{os.getuid()}" if hasattr(os, "getuid") else "kapture"


# ─── USER SETTINGS (persisted to ~/.config/kapture/config.json) ───────────────

_CONFIG_PATH = os.path.expanduser("~/.config/kapture/config.json")
# Where we stash GNOME's original screenshot keybindings before we steal `Print`,
# so we can hand them back when Kapture's hotkey changes or the app is removed.
_GNOME_SS_BACKUP = os.path.expanduser("~/.config/kapture/gnome_screenshot_backup.json")
_CONFIG_DEFAULTS = {
    "save_dir": os.path.expanduser("~/Pictures"),
    "auto_save": False,                 # Save without showing the file dialog
    "capture_binding": "Print",         # GNOME accelerator for the global shortcut
}


def load_config() -> dict:
    cfg = dict(_CONFIG_DEFAULTS)
    try:
        import json
        with open(_CONFIG_PATH) as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update({k: data[k] for k in _CONFIG_DEFAULTS if k in data})
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict):
    try:
        import json
        os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
        with open(_CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
        log.info("save_config: wrote %s", _CONFIG_PATH)
    except OSError as e:
        log.warning("save_config: %s", e)


CONFIG = load_config()

# Delay between trigger and capture, just long enough for the tray menu /
# startup notification to disappear so they don't land in the screenshot.
CAPTURE_DELAY_MS = 150

# Human-readable hotkey string used in UI labels
_HOTKEY_DISPLAY = " / ".join(HOTKEYS)


# ─── RESOURCE PATH (dev + PyInstaller) ────────────────────────────────────────

def resource_path(relative: str) -> str:
    """
    Return the absolute path to a bundled resource.
    Works both when running from source and when packaged with PyInstaller.
    """
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


# ─── SIGNAL BRIDGE (cross-thread → Qt) ────────────────────────────────────────

class SignalBridge(QObject):
    trigger_screenshot = pyqtSignal()

bridge = SignalBridge()


# ─── OVERLAY WINDOW ───────────────────────────────────────────────────────────

class OverlayWindow(QWidget):
    """
    Fullscreen dark overlay for region selection.
    Drag to select, release to capture.
    """

    screenshot_taken = pyqtSignal(QPixmap, QRect)

    def __init__(self, result: 'CaptureResult'):
        log.debug("OverlayWindow.__init__: initializing")
        super().__init__()
        self.result = result
        self.full_pixmap = result.pixmap        # devicePixelRatio already stamped
        self.origin = QPoint()
        self.selection = QRect()
        self.is_drawing = False
        self._setup_ui()

    def _setup_ui(self):
        log.debug("OverlayWindow._setup_ui: configuring fullscreen overlay")
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.BypassWindowManagerHint
        )
        self.setCursor(QCursor(Qt.CrossCursor))

        # Cover all screens using LOGICAL virtual geometry (the captured pixmap
        # carries devicePixelRatio, so logical coords map 1:1 when painted).
        screen_geo = self.result.geometry
        self.setGeometry(screen_geo)
        self.showFullScreen()
        log.debug("OverlayWindow._setup_ui: shown at %s", screen_geo)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Draw the captured screen as base
        painter.drawPixmap(0, 0, self.full_pixmap)

        # Darken overlay (stronger so the bright selection clearly stands out)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 150))

        if not self.selection.isNull() and self.is_drawing:
            sel = self.selection.normalized()

            # Cut out the selection (bright, not darkened)
            painter.drawPixmap(sel, self.full_pixmap, sel)

            # Soft accent glow so the selection visibly "lifts" off the backdrop
            painter.setBrush(Qt.NoBrush)
            for i, alpha in enumerate((28, 18, 10)):
                grow = 2 + i * 3
                painter.setPen(QPen(QColor(0, 174, 255, alpha), 2 + i * 2))
                painter.drawRect(sel.adjusted(-grow, -grow, grow, grow))

            # Crisp selection border
            painter.setPen(QPen(QColor(0, 174, 255), 2, Qt.SolidLine))
            painter.drawRect(sel)

            # Corner handles
            handle_size = 6
            painter.setBrush(QBrush(QColor(0, 174, 255)))
            corners = [
                sel.topLeft(), sel.topRight(),
                sel.bottomLeft(), sel.bottomRight()
            ]
            for pt in corners:
                painter.drawRect(
                    pt.x() - handle_size//2,
                    pt.y() - handle_size//2,
                    handle_size, handle_size
                )

            # Size label
            label = f"{sel.width()} × {sel.height()}"
            painter.setPen(QPen(Qt.white))
            painter.setFont(QFont("Monospace", 10, QFont.Bold))
            label_x = sel.x()
            label_y = sel.y() - 8 if sel.y() > 20 else sel.y() + sel.height() + 18
            painter.fillRect(label_x, label_y - 14, len(label) * 8 + 8, 18,
                             QColor(0, 0, 0, 160))
            painter.drawText(label_x + 4, label_y, label)

        # Help text (when no selection yet)
        if self.selection.isNull():
            painter.setPen(QPen(Qt.white))
            painter.setFont(QFont("Sans", 14))
            msg = "Drag to select a region   •   ESC to cancel"
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance(msg)
            cx = (self.width() - tw) // 2
            cy = self.height() // 2
            painter.fillRect(cx - 12, cy - 22, tw + 24, 32, QColor(0, 0, 0, 140))
            painter.drawText(cx, cy, msg)

    def mousePressEvent(self, event):
        log.debug("OverlayWindow.mousePressEvent: pos=%s", event.pos())
        if event.button() == Qt.LeftButton:
            self.origin = event.pos()
            self.selection = QRect(self.origin, QSize(0, 0))
            self.is_drawing = True
            self.update()

    def mouseMoveEvent(self, event):
        if self.is_drawing:
            self.selection = QRect(self.origin, event.pos())
            self.update()
            log.debug("OverlayWindow.mouseMoveEvent: selection=%s", self.selection.normalized())

    def mouseReleaseEvent(self, event):
        log.debug("OverlayWindow.mouseReleaseEvent")
        if event.button() == Qt.LeftButton and self.is_drawing:
            self.is_drawing = False
            sel = self.selection.normalized()
            if sel.width() > 5 and sel.height() > 5:
                log.info("OverlayWindow.mouseReleaseEvent: region selected %dx%d at (%d,%d)",
                         sel.width(), sel.height(), sel.x(), sel.y())
                # DPR-correct crop: QPixmap.copy() works in device pixels, so the
                # logical selection is scaled up by dpr inside crop_logical().
                cropped = self.result.crop_logical(sel)
                QApplication.clipboard().setPixmap(cropped)
                log.info("OverlayWindow.mouseReleaseEvent: auto-copied to clipboard")
                self.close()
                self.screenshot_taken.emit(cropped, sel)
            else:
                log.debug("OverlayWindow.mouseReleaseEvent: selection too small, discarding")
                self.close()

    def keyPressEvent(self, event):
        log.debug("OverlayWindow.keyPressEvent: key=%s", event.key())
        if event.key() == Qt.Key_Escape:
            log.info("OverlayWindow.keyPressEvent: ESC pressed, closing overlay")
            self.close()


# ─── ANNOTATION CANVAS ────────────────────────────────────────────────────────

class _AnnotationCanvas(QWidget):
    """Drawing surface — handles all mouse events for annotation."""

    def __init__(self, aw: 'AnnotationWindow'):
        log.debug("_AnnotationCanvas.__init__")
        super().__init__(aw)
        self.aw = aw
        self.setFixedSize(aw.base_pixmap.size())
        self.setMouseTracking(True)
        self.setCursor(QCursor(Qt.CrossCursor))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        # Clip the top corners so the image follows the rounded card.
        radius = AnnotationWindow.CARD_RADIUS - 1
        r = QRectF(self.rect())
        path = QPainterPath()
        path.moveTo(r.left(), r.bottom())
        path.lineTo(r.left(), r.top() + radius)
        path.quadTo(r.left(), r.top(), r.left() + radius, r.top())
        path.lineTo(r.right() - radius, r.top())
        path.quadTo(r.right(), r.top(), r.right(), r.top() + radius)
        path.lineTo(r.right(), r.bottom())
        path.closeSubpath()
        p.setClipPath(path)

        p.drawPixmap(0, 0, self.aw.base_pixmap)
        p.drawPixmap(0, 0, self.aw.overlay)
        if self.aw.drawing:
            self.aw._paint_live(p)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.aw.drawing = True
            self.aw.start_pt = event.pos()
            self.aw.cur_pt = event.pos()
            self.aw.pen_path = [event.pos()]
            log.debug("_AnnotationCanvas.mousePressEvent: start=%s tool=%s",
                      event.pos(), self.aw.tool)
            event.accept()

    def mouseMoveEvent(self, event):
        if self.aw.drawing and event.buttons() & Qt.LeftButton:
            self.aw.cur_pt = event.pos()
            if self.aw.tool == AnnotationWindow.TOOL_PEN:
                self.aw.pen_path.append(event.pos())
            self.update()
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.aw.drawing:
            self.aw.drawing = False
            self.aw.cur_pt = event.pos()
            self.aw._commit()
            self.update()
            log.debug("_AnnotationCanvas.mouseReleaseEvent: shape committed")
            event.accept()


# ─── DRAG HANDLE ──────────────────────────────────────────────────────────────

class _DragHandle(QWidget):
    """Toolbar strip that doubles as the window drag handle."""

    def __init__(self, aw: 'AnnotationWindow'):
        super().__init__(aw)
        self.aw = aw

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.aw._drag_offset = event.globalPos() - self.aw.frameGeometry().topLeft()
            self.aw._dragging = True
            log.debug("_DragHandle.mousePressEvent: drag started")
            event.accept()

    def mouseMoveEvent(self, event):
        if self.aw._dragging and event.buttons() & Qt.LeftButton:
            self.aw.move(event.globalPos() - self.aw._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.aw._dragging:
            self.aw._dragging = False
            AnnotationWindow._last_pos = self.aw.frameGeometry().topLeft()
            log.debug("_DragHandle.mouseReleaseEvent: saved pos %s", AnnotationWindow._last_pos)
            event.accept()


# ─── ANNOTATION WINDOW ────────────────────────────────────────────────────────

class AnnotationWindow(QWidget):
    """
    Floating annotation window shown after region capture.
    Displays the captured region with pen/arrow/box drawing tools.
    Draggable from the toolbar; remembers last position across captures.
    """

    TOOL_PEN      = 'pen'
    TOOL_ARROW    = 'arrow'
    TOOL_RECT     = 'rect'
    TOOL_PIXELATE = 'pixelate'
    TOOLBAR_H  = 52
    CARD_RADIUS   = 12
    SHADOW_MARGIN = 24     # transparent border around the card for the drop shadow

    _last_pos: QPoint = None

    # ── stylesheets (modern, flat, accent #00aeff) ────────────────────────────
    _ICON_BTN_CSS = """
        QPushButton {
            background: #1b1b2b; color: #d7d7f0;
            border: 1px solid transparent; border-radius: 8px;
            font-size: 16px;
            min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px;
        }
        QPushButton:hover   { background: #26263d; color: #ffffff; }
        QPushButton:pressed { background: #2e2e4a; }
        QPushButton:checked { background: #00aeff; color: #04121d; border-color: #00aeff; }
    """
    _PILL_BTN_CSS = """
        QPushButton {
            background: #1b1b2b; color: #d7d7f0;
            border: 1px solid #2f2f4d; border-radius: 9px;
            padding: 6px 16px; font-size: 13px; font-weight: 600;
        }
        QPushButton:hover   { background: #26263d; border-color: #3a3a5e; color: #fff; }
        QPushButton:pressed { background: #2e2e4a; }
    """
    _PILL_ACCENT_CSS = """
        QPushButton {
            background: #00aeff; color: #04121d;
            border: 1px solid #00aeff; border-radius: 9px;
            padding: 6px 18px; font-size: 13px; font-weight: 700;
        }
        QPushButton:hover   { background: #2bbcff; border-color: #2bbcff; }
        QPushButton:pressed { background: #009fe6; }
    """
    # Floating close button overlaid on the top-right of the screenshot.
    _CLOSE_BTN_CSS = """
        QPushButton {
            background: rgba(14, 14, 24, 0.82); color: #ffffff;
            border: 1px solid rgba(255, 255, 255, 0.22); border-radius: 14px;
            font-size: 13px; font-weight: 700;
        }
        QPushButton:hover  { background: #ff5470; border-color: #ff5470; }
        QPushButton:pressed { background: #e23c58; }
    """

    def __init__(self, pixmap: QPixmap, region: QRect):
        log.debug("AnnotationWindow.__init__: pixmap=%dx%d", pixmap.width(), pixmap.height())
        super().__init__()
        self.base_pixmap = pixmap
        self.region = region
        self.tool = self.TOOL_PEN
        self.color = QColor(230, 50, 50)
        self.pen_size = 3

        self.drawing = False
        self.start_pt = QPoint()
        self.cur_pt = QPoint()
        self.pen_path: list = []

        self.overlay = QPixmap(pixmap.size())
        self.overlay.fill(Qt.transparent)

        self._drag_offset = QPoint()
        self._dragging = False
        self._tool_btns: dict = {}
        self._undo_stack: list = []   # overlay snapshots we can revert to
        self._redo_stack: list = []   # snapshots we can re-apply
        self.card = None

        self._setup_ui()
        self._position_window()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        log.debug("AnnotationWindow._setup_ui: building")
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        # Outer layout reserves space for the drop shadow.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(self.SHADOW_MARGIN, self.SHADOW_MARGIN,
                                 self.SHADOW_MARGIN, self.SHADOW_MARGIN)
        outer.setSpacing(0)

        # Card — the visible rounded panel that floats above the desktop.
        # A bright accent ring + deep shadow make it unmistakably a floating
        # window (otherwise the screenshot blends into the desktop behind it).
        self.card = QWidget()
        self.card.setObjectName("card")
        self.card.setStyleSheet(f"""
            QWidget#card {{
                background: #0e0e18;
                border: 2px solid #00aeff;
                border-radius: {self.CARD_RADIUS}px;
            }}
        """)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(60)
        shadow.setColor(QColor(0, 0, 0, 235))
        shadow.setOffset(0, 14)
        self.card.setGraphicsEffect(shadow)
        outer.addWidget(self.card)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(2, 2, 2, 2)
        card_layout.setSpacing(0)

        # Canvas
        self.canvas = _AnnotationCanvas(self)
        card_layout.addWidget(self.canvas)

        # Floating close button — top-right of the screenshot (fix: cross on top).
        self.close_btn = QPushButton("✕", self.card)
        self.close_btn.setObjectName("topClose")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setFixedSize(28, 28)
        self.close_btn.setStyleSheet(self._CLOSE_BTN_CSS)
        self.close_btn.setToolTip("Close  ·  Esc")
        self.close_btn.clicked.connect(self.close)

        # Toolbar (also the drag handle)
        tb_widget = _DragHandle(self)
        tb_widget.setObjectName("toolbar")
        tb_widget.setFixedHeight(self.TOOLBAR_H)
        tb_widget.setStyleSheet(f"""
            QWidget#toolbar {{
                background: #14141f;
                border-top: 1px solid #23233a;
                border-bottom-left-radius: {self.CARD_RADIUS - 1}px;
                border-bottom-right-radius: {self.CARD_RADIUS - 1}px;
            }}
        """)
        tb = QHBoxLayout(tb_widget)
        tb.setContentsMargins(12, 8, 12, 8)
        tb.setSpacing(6)

        # Drawing tools
        for icon, tool, tip in [("✏", self.TOOL_PEN, "Pen  ·  P"),
                                ("↗", self.TOOL_ARROW, "Arrow  ·  A"),
                                ("▭", self.TOOL_RECT, "Rectangle  ·  R"),
                                ("▦", self.TOOL_PIXELATE, "Pixelate / redact  ·  B")]:
            btn = QPushButton(icon)
            btn.setCheckable(True)
            btn.setChecked(tool == self.tool)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(self._ICON_BTN_CSS)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _, t=tool: self._select_tool(t))
            self._tool_btns[tool] = btn
            tb.addWidget(btn)

        # Colour swatch
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(34, 34)
        self.color_btn.setCursor(Qt.PointingHandCursor)
        self.color_btn.setToolTip("Pick colour")
        self.color_btn.clicked.connect(self._pick_color)
        self._refresh_color_btn()
        tb.addWidget(self.color_btn)

        tb.addWidget(self._separator())

        # Undo / Redo
        self.undo_btn = QPushButton("⟲")
        self.undo_btn.setCursor(Qt.PointingHandCursor)
        self.undo_btn.setStyleSheet(self._ICON_BTN_CSS)
        self.undo_btn.setToolTip("Undo  ·  Ctrl+Z")
        self.undo_btn.clicked.connect(self._undo)
        tb.addWidget(self.undo_btn)

        self.redo_btn = QPushButton("⟳")
        self.redo_btn.setCursor(Qt.PointingHandCursor)
        self.redo_btn.setStyleSheet(self._ICON_BTN_CSS)
        self.redo_btn.setToolTip("Redo  ·  Ctrl+Shift+Z")
        self.redo_btn.clicked.connect(self._redo)
        tb.addWidget(self.redo_btn)

        tb.addStretch()

        # Copy (secondary) + Save (primary accent)
        self.copy_btn = QPushButton("⧉  Copy")
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.setStyleSheet(self._PILL_BTN_CSS)
        self.copy_btn.setToolTip("Copy annotated image to clipboard  ·  Ctrl+C")
        self.copy_btn.clicked.connect(self._copy_to_clipboard)
        tb.addWidget(self.copy_btn)

        self.save_btn = QPushButton("⭳  Save")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.setStyleSheet(self._PILL_ACCENT_CSS)
        self.save_btn.setToolTip("Save annotated image  ·  Ctrl+S")
        self.save_btn.clicked.connect(lambda: asyncio.ensure_future(self._save_to_file()))
        tb.addWidget(self.save_btn)

        card_layout.addWidget(tb_widget)
        self.adjustSize()
        self._position_close_btn()
        self._install_shortcuts()
        log.debug("AnnotationWindow._setup_ui: done, size=%s", self.size())

    def _position_close_btn(self):
        """Pin the close button to the top-right corner of the window, exactly
        like a standard window close button."""
        if self.card is None:
            return
        inset = 10
        x = self.card.width() - self.close_btn.width() - inset
        y = inset
        self.close_btn.move(x, y)
        self.close_btn.raise_()

    def showEvent(self, event):
        super().showEvent(event)
        self._position_close_btn()
        # Re-pin after the layout fully settles (card width is final by then).
        QTimer.singleShot(0, self._position_close_btn)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_close_btn()

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setFixedWidth(1)
        line.setStyleSheet("background: #26263d; border: none; margin: 5px 3px;")
        return line

    def _install_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+C"), self, activated=self._copy_to_clipboard)
        QShortcut(QKeySequence("Ctrl+S"), self,
                  activated=lambda: asyncio.ensure_future(self._save_to_file()))
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self._undo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, activated=self._redo)
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=self._redo)
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.close)
        QShortcut(QKeySequence("P"), self, activated=lambda: self._select_tool(self.TOOL_PEN))
        QShortcut(QKeySequence("A"), self, activated=lambda: self._select_tool(self.TOOL_ARROW))
        QShortcut(QKeySequence("R"), self, activated=lambda: self._select_tool(self.TOOL_RECT))
        QShortcut(QKeySequence("B"), self, activated=lambda: self._select_tool(self.TOOL_PIXELATE))

    def _toast(self, text: str):
        """Brief, self-dismissing pill notification centred over the canvas."""
        lbl = QLabel(text, self.card)
        lbl.setStyleSheet(
            "background: rgba(0, 174, 255, 235); color: #04121d;"
            "font-size: 12px; font-weight: 700; padding: 7px 16px; border-radius: 9px;"
        )
        lbl.adjustSize()
        x = max(8, (self.canvas.width() - lbl.width()) // 2)
        y = max(8, self.canvas.height() - lbl.height() - 16)
        lbl.move(x, y)
        lbl.show()
        lbl.raise_()
        QTimer.singleShot(1300, lbl.deleteLater)

    def _select_tool(self, tool: str):
        log.debug("AnnotationWindow._select_tool: %s", tool)
        self.tool = tool
        for t, b in self._tool_btns.items():
            b.setChecked(t == tool)

    def _refresh_color_btn(self):
        self.color_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.color.name()};
                border: 2px solid #2f2f4d; border-radius: 8px;
                min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px;
            }}
            QPushButton:hover {{ border-color: #00aeff; }}
        """)

    def _push_undo(self):
        """Snapshot the overlay before a new edit; invalidates the redo stack."""
        self._undo_stack.append(self.overlay.copy())
        if len(self._undo_stack) > 40:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _undo(self):
        if not self._undo_stack:
            self._toast("Nothing to undo")
            return
        self._redo_stack.append(self.overlay.copy())
        self.overlay = self._undo_stack.pop()
        self.canvas.update()
        log.debug("AnnotationWindow._undo: %d undo / %d redo left",
                  len(self._undo_stack), len(self._redo_stack))

    def _redo(self):
        if not self._redo_stack:
            self._toast("Nothing to redo")
            return
        self._undo_stack.append(self.overlay.copy())
        self.overlay = self._redo_stack.pop()
        self.canvas.update()
        log.debug("AnnotationWindow._redo: %d undo / %d redo left",
                  len(self._undo_stack), len(self._redo_stack))

    def _pixelate_region(self, rect: QRect):
        """Redact a region by mosaic (downscale → upscale with nearest-neighbour).

        Pixelation is committed straight onto the overlay layer, so it bakes into
        the exported PNG and cannot be peeled off — and unlike a heavy blur it is
        not reversible, which is what you want for hiding secrets.
        """
        rect = rect.normalized().intersected(self.overlay.rect())
        if rect.width() < 4 or rect.height() < 4:
            return
        # Sample whatever is currently visible (base + existing annotations).
        composite = QPixmap(self.base_pixmap)
        cp = QPainter(composite)
        cp.drawPixmap(0, 0, self.overlay)
        cp.end()

        patch = composite.copy(rect)
        block = 9                                  # ~mosaic cell size in px
        sw = max(1, rect.width() // block)
        sh = max(1, rect.height() // block)
        small = patch.scaled(sw, sh, Qt.IgnoreAspectRatio, Qt.FastTransformation)
        mosaic = small.scaled(rect.width(), rect.height(),
                              Qt.IgnoreAspectRatio, Qt.FastTransformation)

        p = QPainter(self.overlay)
        p.drawPixmap(rect.topLeft(), mosaic)
        p.end()
        log.debug("AnnotationWindow._pixelate_region: redacted %dx%d at (%d,%d)",
                  rect.width(), rect.height(), rect.x(), rect.y())

    def _copy_to_clipboard(self):
        QApplication.clipboard().setPixmap(self._final_pixmap())
        self._toast("Copied to clipboard")
        log.info("AnnotationWindow._copy_to_clipboard: annotated image copied")

    def _pick_color(self):
        log.debug("AnnotationWindow._pick_color: opening dialog")
        col = QColorDialog.getColor(self.color, self, "Pick Annotation Color")
        if col.isValid():
            self.color = col
            self._refresh_color_btn()
            log.info("AnnotationWindow._pick_color: chosen %s", col.name())

    # ── drawing ───────────────────────────────────────────────────────────────

    def _make_pen(self) -> QPen:
        return QPen(self.color, self.pen_size, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)

    def _paint_live(self, painter: QPainter):
        if self.tool == self.TOOL_PIXELATE:
            # Preview the redaction area (the mosaic is applied on release).
            r = QRect(self.start_pt, self.cur_pt).normalized()
            painter.fillRect(r, QColor(0, 0, 0, 95))
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(0, 174, 255), 1, Qt.DashLine))
            painter.drawRect(r)
            return
        painter.setPen(self._make_pen())
        painter.setBrush(Qt.NoBrush)
        if self.tool == self.TOOL_PEN and len(self.pen_path) > 1:
            for i in range(len(self.pen_path) - 1):
                painter.drawLine(self.pen_path[i], self.pen_path[i + 1])
        elif self.tool == self.TOOL_ARROW:
            self._paint_arrow(painter, self.start_pt, self.cur_pt)
        elif self.tool == self.TOOL_RECT:
            painter.drawRect(QRect(self.start_pt, self.cur_pt).normalized())

    def _paint_arrow(self, painter: QPainter, start: QPoint, end: QPoint):
        dx, dy = end.x() - start.x(), end.y() - start.y()
        if math.hypot(dx, dy) < 2:
            return
        painter.setPen(self._make_pen())
        painter.drawLine(start, end)
        angle = math.atan2(dy, dx)
        sz = max(12, self.pen_size * 4)
        p1 = QPoint(int(end.x() - sz * math.cos(angle - math.pi / 6)),
                    int(end.y() - sz * math.sin(angle - math.pi / 6)))
        p2 = QPoint(int(end.x() - sz * math.cos(angle + math.pi / 6)),
                    int(end.y() - sz * math.sin(angle + math.pi / 6)))
        painter.setBrush(QBrush(self.color))
        painter.setPen(Qt.NoPen)
        painter.drawPolygon(QPolygon([end, p1, p2]))

    def _commit(self):
        log.debug("AnnotationWindow._commit: tool=%s", self.tool)
        # Snapshot the overlay BEFORE the edit so it can be undone/redone.
        self._push_undo()

        if self.tool == self.TOOL_PIXELATE:
            self._pixelate_region(QRect(self.start_pt, self.cur_pt))
            self.pen_path = []
            return

        p = QPainter(self.overlay)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(self._make_pen())
        p.setBrush(Qt.NoBrush)
        if self.tool == self.TOOL_PEN and len(self.pen_path) > 1:
            for i in range(len(self.pen_path) - 1):
                p.drawLine(self.pen_path[i], self.pen_path[i + 1])
        elif self.tool == self.TOOL_ARROW:
            self._paint_arrow(p, self.start_pt, self.cur_pt)
        elif self.tool == self.TOOL_RECT:
            p.drawRect(QRect(self.start_pt, self.cur_pt).normalized())
        p.end()
        self.pen_path = []

    def _final_pixmap(self) -> QPixmap:
        result = QPixmap(self.base_pixmap)
        p = QPainter(result)
        p.drawPixmap(0, 0, self.overlay)
        p.end()
        return result

    # ── positioning ───────────────────────────────────────────────────────────

    def _position_window(self):
        log.debug("AnnotationWindow._position_window: positioning")
        if AnnotationWindow._last_pos is not None:
            log.debug("AnnotationWindow._position_window: restoring %s", AnnotationWindow._last_pos)
            self.move(AnnotationWindow._last_pos)
        else:
            # Offset by the shadow margin so the visible card (not the transparent
            # window) lines up with the captured region.
            screen = QApplication.primaryScreen().geometry()
            m = self.SHADOW_MARGIN
            x = max(0, min(self.region.x() - m, screen.right() - self.width() + m))
            y = max(0, min(self.region.y() - m, screen.bottom() - self.height() + m))
            self.move(x, y)
        self.show()
        self.raise_()
        log.debug("AnnotationWindow._position_window: shown at %s", self.pos())

    # ── save ──────────────────────────────────────────────────────────────────

    async def _save_to_file(self):
        ts = time.strftime("%Y%m%d_%H%M%S")
        final = self._final_pixmap()

        # Auto-save: skip the dialog and write straight to the configured folder.
        if CONFIG.get("auto_save"):
            save_dir = CONFIG.get("save_dir") or os.path.expanduser("~/Pictures")
            try:
                os.makedirs(save_dir, exist_ok=True)
            except OSError:
                save_dir = os.path.expanduser("~")
            path = os.path.join(save_dir, f"kapture_{ts}.png")
            log.info("AnnotationWindow._save_to_file: auto-saving to %s", path)
            await asyncio.to_thread(final.save, path)
            self._toast(f"Saved  ·  {os.path.basename(path)}")
            return

        log.info("AnnotationWindow._save_to_file: opening dialog")
        default = os.path.join(CONFIG.get("save_dir") or os.path.expanduser("~/Pictures"),
                               f"kapture_{ts}.png")
        # Create dialog explicitly so we can clear the inherited dark stylesheet
        dlg = QFileDialog(None, "Save Screenshot", default,
                          "PNG Image (*.png);;JPEG Image (*.jpg);;All Files (*)")
        dlg.setAcceptMode(QFileDialog.AcceptSave)
        dlg.setStyleSheet("")   # use system default — no inherited dark theme
        if dlg.exec_() == QFileDialog.Accepted:
            files = dlg.selectedFiles()
            path = files[0] if files else ""
        else:
            path = ""
        if path:
            log.info("AnnotationWindow._save_to_file: saving to %s", path)
            await asyncio.to_thread(final.save, path)
            self._toast(f"Saved  ·  {os.path.basename(path)}")
            log.info("AnnotationWindow._save_to_file: done")
        else:
            log.debug("AnnotationWindow._save_to_file: cancelled")


# ─── CAPTURE ENGINE (native, dependency-free) ─────────────────────────────────

# XDG desktop portal D-Bus constants (Wayland capture path)
_PORTAL_SERVICE   = "org.freedesktop.portal.Desktop"
_PORTAL_PATH      = "/org/freedesktop/portal/desktop"
_SCREENSHOT_IFACE = "org.freedesktop.portal.Screenshot"
_REQUEST_IFACE    = "org.freedesktop.portal.Request"

# Kapture's bundled GNOME Shell extension exposes a flash-free, prompt-free
# capture over D-Bus from INSIDE the Shell's privileged context — the only way
# to avoid GNOME's shutter flash on Wayland (Mutter refuses Shell.Screenshot for
# external apps, the portal always flashes, and grim needs wlr-screencopy).
# Installed by the .deb to /usr/share/gnome-shell/extensions; enabled per user.
_KAP_EXT_UUID    = "kapture-screenshot@yeakiniqra.github.io"
_KAP_EXT_SERVICE = "org.kapture.ScreenshotHelper"
_KAP_EXT_PATH    = "/org/kapture/ScreenshotHelper"
_KAP_EXT_IFACE   = "org.kapture.ScreenshotHelper"

# GNOME's own screenshot D-Bus interface.  On GNOME 41+ Mutter refuses this for
# unconfined callers ("Screenshot is not allowed"); kept only as a best-effort
# path for older GNOME where it still works flash-free (flash=False).
_GS_SERVICE = "org.gnome.Shell.Screenshot"
_GS_PATH    = "/org/gnome/Shell/Screenshot"
_GS_IFACE   = "org.gnome.Shell.Screenshot"

# On Wayland (GNOME/KDE) the portal shows a one-time "Allow … to take
# screenshots?" consent prompt on first use; the Response signal only arrives
# after the user answers it.  The timeout therefore has to allow human reaction
# time — it is a safety net for a genuinely stuck portal, not a UX deadline.
# (Once granted, the permission is remembered and later captures are instant.)
_PORTAL_TIMEOUT_S = 120.0


class CaptureResult:
    """A full virtual-desktop grab plus the geometry/scaling an overlay needs."""

    __slots__ = ("pixmap", "geometry", "dpr")

    def __init__(self, pixmap: QPixmap, geometry: QRect, dpr: float):
        self.pixmap = pixmap        # devicePixelRatio is stamped on this pixmap
        self.geometry = geometry    # LOGICAL virtual geometry (overlay coords)
        self.dpr = dpr              # device pixels per logical pixel

    def crop_logical(self, sel: QRect) -> QPixmap:
        """
        Crop using a LOGICAL selection rect.

        QPixmap.copy() ignores devicePixelRatio and works in raw *device* pixels,
        so the logical selection is scaled up by dpr before copying and the ratio
        is re-stamped on the result.  (Contrast OverlayWindow.paintEvent's
        drawPixmap, whose source rect IS interpreted in logical coords for a
        dpr-stamped pixmap — do NOT scale there.  The two Qt APIs use different
        conventions on purpose; scaling both would double-scale.)
        """
        d = self.dpr
        dev = QRect(round(sel.x() * d), round(sel.y() * d),
                    round(sel.width() * d), round(sel.height() * d))
        cropped = self.pixmap.copy(dev)
        cropped.setDevicePixelRatio(d)
        return cropped


class _PortalResponseReceiver(QObject):
    """
    One-shot receiver for a single portal Request's Response signal.
    Created per capture so overlapping requests never clobber each other's
    future or signal subscription.
    """

    def __init__(self, fut: asyncio.Future):
        super().__init__()
        self._fut = fut

    @pyqtSlot(QDBusMessage)
    def on_response(self, msg: QDBusMessage):
        # Response(u response, a{sv} results)   response: 0=ok 1=cancel 2=error
        args = msg.arguments()
        if len(args) < 2:
            log.warning("_PortalResponseReceiver: Response had %d args", len(args))
            return
        response = int(args[0])
        results = dict(args[1]) if args[1] else {}
        log.debug("_PortalResponseReceiver: response=%s keys=%s", response, list(results))
        if not self._fut.done():
            # Hand the value back to the awaiting coroutine on its own loop.
            self._fut.get_loop().call_soon_threadsafe(
                self._fut.set_result, (response, results)
            )


class ScreenshotEngine(QObject):
    """
    Modern, in-process full-desktop capture — no external screenshot binaries.

        X11 / XWayland-backed grab  ->  QScreen.grabWindow        (instant, no flash)
        GNOME Wayland               ->  Kapture Shell extension    (flash-free)
        GNOME Wayland (fallback)    ->  XDG desktop portal         (flashes + prompts)
        KDE / other Wayland         ->  XDG desktop portal
        wlroots (Sway/Hyprland)     ->  grim
        nothing worked              ->  None  (caller shows a clear error dialog)

    QtDBus ships inside PyQt5, so the Wayland paths add no new pip dependency, and
    the capture never spawns gnome-screenshot/scrot.  On GNOME Wayland the shutter
    flash and consent prompt are unavoidable for an unconfined app via the portal,
    so the bundled Shell extension provides a privileged flash-free path instead;
    the portal remains the fallback until that extension is enabled.
    """

    _token_counter = 0

    def __init__(self):
        super().__init__()
        self._busy = False
        self._anchor: 'QWidget | None' = None   # realized X11 window for portal parenting

    @property
    def busy(self) -> bool:
        return self._busy

    # ── detection ───────────────────────────────────────────────────────────

    @staticmethod
    def _session_is_wayland() -> bool:
        """
        Detect the *real* session — NOT QGuiApplication.platformName().

        On GNOME, Qt logs "Ignoring XDG_SESSION_TYPE=wayland on Gnome" and
        reports platformName()=='xcb' (XWayland).  grabWindow over that XWayland
        surface returns a BLACK frame, so trusting platformName would silently
        produce black screenshots.  We key off the session environment instead.
        """
        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            return True
        if os.environ.get("WAYLAND_DISPLAY"):
            return True
        return "wayland" in os.environ.get("QT_QPA_PLATFORM", "").lower()

    @staticmethod
    def _portal_available() -> bool:
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return False
        iface = QDBusInterface(_PORTAL_SERVICE, _PORTAL_PATH, _SCREENSHOT_IFACE, bus)
        ok = iface.isValid()
        log.debug("ScreenshotEngine._portal_available: %s", ok)
        return ok

    # ── public entry point ──────────────────────────────────────────────────

    async def capture_full_screen(self) -> 'CaptureResult | None':
        if self._busy:
            log.warning("capture_full_screen: a capture is already in flight, ignoring")
            return None
        self._busy = True
        try:
            wayland = self._session_is_wayland()
            log.info("capture_full_screen: wayland_session=%s platform=%s",
                     wayland, QGuiApplication.platformName())

            if not wayland:
                return self._capture_x11_grab()

            # Wayland capture order, best UX first:
            #   1. Kapture Shell extension  — flash-free, no prompt (GNOME Wayland)
            #   2. org.gnome.Shell.Screenshot flash=False — older GNOME only
            #   3. XDG portal               — always works (GNOME flashes + prompts)
            #   4. grim                     — wlroots compositors (Sway/Hyprland)
            # Never a silent black grabWindow.
            ext = self._capture_kapture_extension()
            if ext is not None:
                return ext
            shell = self._capture_gnome_shell()
            if shell is not None:
                return shell
            if self._portal_available():
                try:
                    return await self._capture_wayland_portal()
                except Exception as e:
                    log.error("capture_full_screen: portal failed: %s", e)
            grim = self._capture_grim()
            if grim is not None:
                return grim
            log.error("capture_full_screen: no working Wayland backend")
            return None
        finally:
            self._busy = False

    # ── X11 / XWayland path (instant, flicker-free) ───────────────────────────

    @staticmethod
    def _capture_x11_grab() -> 'CaptureResult | None':
        screen = QApplication.primaryScreen()
        if screen is None:
            log.error("_capture_x11_grab: no primary screen")
            return None
        geo = screen.virtualGeometry()              # logical coords
        dpr = screen.devicePixelRatio()
        pixmap = screen.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())
        if pixmap.isNull():
            log.error("_capture_x11_grab: null pixmap")
            return None
        pixmap.setDevicePixelRatio(dpr)
        log.info("_capture_x11_grab: %dx%d px (logical %dx%d, dpr=%.2f)",
                 pixmap.width(), pixmap.height(), geo.width(), geo.height(), dpr)
        return CaptureResult(pixmap, geo, dpr)

    # ── Kapture Shell-extension path (flash-free on GNOME Wayland) ────────────

    def _capture_kapture_extension(self) -> 'CaptureResult | None':
        """
        Flash-free capture via Kapture's GNOME Shell extension
        (org.kapture.ScreenshotHelper).  The extension runs inside the Shell's
        privileged context, where Shell.Screenshot is permitted and no shutter
        flash fires.  Returns None if the helper isn't loaded yet (the user has
        not logged out/in since install), so the portal path can take over.
        """
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return None
        iface = QDBusInterface(_KAP_EXT_SERVICE, _KAP_EXT_PATH, _KAP_EXT_IFACE, bus)
        if not iface.isValid():
            log.debug("_capture_kapture_extension: helper not running")
            return None
        iface.setTimeout(5000)

        fd, tmp = tempfile.mkstemp(suffix=".png", prefix="kapture_")
        os.close(fd)
        try:
            reply = iface.call("CaptureToFile", tmp)
            if reply.type() == QDBusMessage.ErrorMessage:
                log.debug("_capture_kapture_extension: call error: %s", reply.errorMessage())
                return None
            args = reply.arguments()
            if not (args and bool(args[0])):
                log.warning("_capture_kapture_extension: helper reported failure")
                return None
            pixmap = QPixmap(tmp)
            if pixmap.isNull():
                log.warning("_capture_kapture_extension: null pixmap from %s", tmp)
                return None
            log.info("_capture_kapture_extension: captured %dx%d (flash-free)",
                     pixmap.width(), pixmap.height())
            return self._wrap_full_pixmap(pixmap)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # ── GNOME Shell path (older GNOME; flash-free where still permitted) ──────

    def _capture_gnome_shell(self) -> 'CaptureResult | None':
        """
        Capture via org.gnome.Shell.Screenshot with flash=False — no shutter
        animation and no portal consent dialog.

        Present only on GNOME, and callable only by an unconfined process (a
        normal .deb install).  Under snap/flatpak confinement the call is refused;
        we return None so the portal path takes over.  A single fast D-Bus
        round-trip writes the PNG to a temp file.
        """
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return None
        iface = QDBusInterface(_GS_SERVICE, _GS_PATH, _GS_IFACE, bus)
        if not iface.isValid():
            log.debug("_capture_gnome_shell: org.gnome.Shell.Screenshot unavailable")
            return None
        iface.setTimeout(5000)

        fd, tmp = tempfile.mkstemp(suffix=".png", prefix="kapture_")
        os.close(fd)
        try:
            # Screenshot(in b include_cursor, in b flash, in s filename)
            #            -> (b success, s filename_used)
            reply = iface.call("Screenshot", False, False, tmp)
            if reply.type() == QDBusMessage.ErrorMessage:
                log.debug("_capture_gnome_shell: refused: %s", reply.errorMessage())
                return None
            args = reply.arguments()
            success = bool(args[0]) if args else False
            used = args[1] if len(args) > 1 and args[1] else tmp
            if not success:
                log.warning("_capture_gnome_shell: shell reported failure")
                return None
            pixmap = QPixmap(used)
            if used != tmp:
                try:
                    os.unlink(used)
                except OSError:
                    pass
            if pixmap.isNull():
                log.warning("_capture_gnome_shell: null pixmap from %s", used)
                return None
            log.info("_capture_gnome_shell: captured %dx%d (flash-free)",
                     pixmap.width(), pixmap.height())
            return self._wrap_full_pixmap(pixmap)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # ── Wayland portal path (no binary; GNOME flashes, KDE/others don't) ──────

    @classmethod
    def _next_token(cls) -> str:
        cls._token_counter += 1
        return f"kapture_{os.getpid()}_{cls._token_counter}"

    @staticmethod
    def _sender_token(bus: QDBusConnection) -> str:
        name = bus.baseService()        # ':1.210'
        if name.startswith(":"):
            name = name[1:]
        return name.replace(".", "_")   # '1_210' (used in the Request object path)

    def _parent_window_token(self) -> str:
        """
        Build an 'x11:<xid>' parent-window token from a realized native window.

        GNOME's portal associates the screenshot Request with this window; an
        empty token has been observed to make the request fail with response=2
        ("Failed to associate portal window with parent window").  Since Qt runs
        as XWayland on GNOME (platformName 'xcb'), every window has an X11 id we
        can hand to the portal.  Returns '' when no X11 id is obtainable.
        """
        if QGuiApplication.platformName() != "xcb":
            return ""
        try:
            if self._anchor is None:
                w = QWidget()
                w.setAttribute(Qt.WA_DontShowOnScreen, True)
                w.resize(1, 1)
                w.show()                # realizes a native window, off-screen
                self._anchor = w
            xid = int(self._anchor.winId())
            return "x11:%x" % xid if xid else ""
        except Exception as e:
            log.debug("_parent_window_token: %s", e)
            return ""

    async def _capture_wayland_portal(self) -> 'CaptureResult | None':
        loop = asyncio.get_running_loop()
        bus = QDBusConnection.sessionBus()

        token = self._next_token()
        sender = self._sender_token(bus)
        request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

        fut = loop.create_future()
        receiver = _PortalResponseReceiver(fut)
        connected_paths: list = []

        def _connect(path: str):
            if path in connected_paths:
                return
            ok = bus.connect(_PORTAL_SERVICE, path, _REQUEST_IFACE,
                             "Response", receiver.on_response)
            log.debug("_capture_wayland_portal: connect Response on %s -> %s", path, ok)
            if ok:
                connected_paths.append(path)

        # Subscribe to the PREDICTED Request path BEFORE issuing the call so we
        # never miss an answer that arrives before we are listening.
        _connect(request_path)

        options = {
            "handle_token": token,
            "interactive": False,   # capture the whole screen, no selection UI
            "modal": False,
        }
        msg = QDBusMessage.createMethodCall(
            _PORTAL_SERVICE, _PORTAL_PATH, _SCREENSHOT_IFACE, "Screenshot"
        )
        msg.setArguments([self._parent_window_token(), options])

        # Non-blocking call — the canonical result arrives on the Response signal
        # (after the user answers the one-time consent prompt), so we must NOT
        # block the qasync loop on the D-Bus round-trip.  The Request object path
        # is deterministic from sender + handle_token (xdg-desktop-portal spec),
        # so the predicted subscription above is sufficient.
        bus.asyncCall(msg)

        try:
            response, results = await asyncio.wait_for(fut, _PORTAL_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise RuntimeError("portal Response timed out (consent not answered?)")
        finally:
            for p in connected_paths:
                bus.disconnect(_PORTAL_SERVICE, p, _REQUEST_IFACE,
                               "Response", receiver.on_response)
            receiver.deleteLater()

        if response == 1:
            raise RuntimeError("screenshot permission denied / cancelled by user")
        if response != 0:
            raise RuntimeError(f"portal returned error (response={response})")
        uri = results.get("uri", "")
        if not uri:
            raise RuntimeError("portal Response had no uri")

        path = unquote(urlparse(uri).path)
        pixmap = QPixmap(path)
        try:
            os.unlink(path)             # portal stores a throwaway file; clean it up
        except OSError as e:
            log.debug("_capture_wayland_portal: could not unlink %s: %s", path, e)
        if pixmap.isNull():
            raise RuntimeError(f"failed to load portal image {path}")
        return self._wrap_full_pixmap(pixmap)

    # ── grim fallback (wlroots: Sway / Hyprland) ──────────────────────────────

    def _capture_grim(self) -> 'CaptureResult | None':
        if shutil.which("grim") is None:
            log.debug("_capture_grim: grim not installed")
            return None
        fd, tmp = tempfile.mkstemp(suffix=".png", prefix="kapture_")
        os.close(fd)
        try:
            r = subprocess.run(["grim", tmp],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if r.returncode != 0 or os.path.getsize(tmp) == 0:
                log.warning("_capture_grim: grim exited %d", r.returncode)
                return None
            pixmap = QPixmap(tmp)
            if pixmap.isNull():
                log.warning("_capture_grim: loaded a null pixmap")
                return None
            log.info("_capture_grim: captured %dx%d", pixmap.width(), pixmap.height())
            return self._wrap_full_pixmap(pixmap)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # ── shared HiDPI wrapping ─────────────────────────────────────────────────

    @staticmethod
    def _wrap_full_pixmap(pixmap: QPixmap) -> 'CaptureResult':
        """Stamp devicePixelRatio so a file-sourced pixmap (portal/grim) aligns
        1:1 with logical virtual geometry, and wrap it in a CaptureResult."""
        screen = QApplication.primaryScreen()
        geo = screen.virtualGeometry()
        dpr = pixmap.width() / geo.width() if geo.width() else screen.devicePixelRatio()
        dpr = round(dpr, 4) or 1.0
        pixmap.setDevicePixelRatio(dpr)
        log.info("_wrap_full_pixmap: %dx%d px (logical %dx%d, dpr=%.2f)",
                 pixmap.width(), pixmap.height(), geo.width(), geo.height(), dpr)
        return CaptureResult(pixmap, geo, dpr)


# ─── ABOUT DIALOG ─────────────────────────────────────────────────────────────

class AboutDialog(QDialog):
    """Frameless, rounded 'About' card matching the annotation editor's theme."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag = QPoint()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)

        card = QWidget()
        card.setObjectName("aboutCard")
        card.setStyleSheet("""
            QWidget#aboutCard {
                background: #0e0e18;
                border: 1px solid #2a2a44;
                border-radius: 16px;
            }
            QLabel { color: #e7e7fb; background: transparent; border: none; }
        """)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(46)
        shadow.setColor(QColor(0, 0, 0, 200))
        shadow.setOffset(0, 10)
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)

        v = QVBoxLayout(card)
        v.setContentsMargins(26, 24, 26, 20)
        v.setSpacing(14)

        # Header: icon + name + version
        header = QHBoxLayout()
        header.setSpacing(16)
        icon = QLabel()
        icon.setPixmap(
            QIcon(resource_path(os.path.join('assets', 'app-logo.png'))).pixmap(60, 60))
        icon.setFixedSize(60, 60)
        header.addWidget(icon, 0, Qt.AlignTop)

        titlebox = QVBoxLayout()
        titlebox.setSpacing(3)
        name = QLabel(APP_NAME)
        name.setStyleSheet("font-size: 26px; font-weight: 800; color: #ffffff;")
        titlebox.addWidget(name)
        ver = QLabel(f"Version {VERSION}")
        ver.setStyleSheet("font-size: 12px; font-weight: 700; color: #00aeff;")
        titlebox.addWidget(ver)
        header.addLayout(titlebox)
        header.addStretch()
        v.addLayout(header)

        # Description
        desc = QLabel("A Lightshot-style screenshot tool for Linux — capture any "
                      "region, annotate, and share in seconds.")
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 13px; color: #b9b9d6;")
        v.addWidget(desc)

        # Shortcut chips
        chips = QHBoxLayout()
        chips.setSpacing(8)
        cap = QLabel("Shortcut")
        cap.setStyleSheet("color: #7a7a99; font-size: 12px;")
        chips.addWidget(cap)
        for key in HOTKEYS:
            disp = (key.replace("<", "").replace(">", "").replace("_", " ")
                       .title().replace("+", " + "))
            chip = QLabel(disp)
            chip.setStyleSheet(
                "background: #1b1b2b; border: 1px solid #2f2f4d; border-radius: 6px;"
                "padding: 3px 9px; color: #d7d7f0; font-size: 12px; font-weight: 600;")
            chips.addWidget(chip)
        chips.addStretch()
        v.addLayout(chips)

        meta = QLabel(f"Made by {AUTHOR}    ·    MIT License")
        meta.setStyleSheet("color: #7a7a99; font-size: 12px;")
        v.addWidget(meta)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: #23233a; border: none;")
        v.addWidget(line)

        # Buttons
        btns = QHBoxLayout()
        btns.setSpacing(8)
        gh = QPushButton("↗  GitHub")
        gh.setCursor(Qt.PointingHandCursor)
        gh.setStyleSheet(AnnotationWindow._PILL_BTN_CSS)
        gh.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("https://github.com/yeakiniqra/Kapture")))
        btns.addWidget(gh)
        btns.addStretch()
        ok = QPushButton("Close")
        ok.setCursor(Qt.PointingHandCursor)
        ok.setStyleSheet(AnnotationWindow._PILL_ACCENT_CSS)
        ok.clicked.connect(self.accept)
        ok.setDefault(True)
        btns.addWidget(ok)
        v.addLayout(btns)

    # Draggable (frameless) ────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPos() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self.move(e.globalPos() - self._drag)
            e.accept()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(e)


class HelperSetupDialog(QDialog):
    """
    One-time, OPTIONAL prompt shown when the flash-free Shell helper is installed
    but not loaded yet.  Kapture already works (via the portal); this just offers
    to finish the upgrade now instead of waiting for the next natural login.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logout = False
        self._drag = QPoint()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)

        card = QWidget()
        card.setObjectName("card")
        card.setFixedWidth(430)
        card.setStyleSheet("""
            QWidget#card {
                background: #0e0e18; border: 1px solid #2a2a44; border-radius: 16px;
            }
            QLabel { color: #e7e7fb; background: transparent; border: none; }
        """)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(46)
        shadow.setColor(QColor(0, 0, 0, 200))
        shadow.setOffset(0, 10)
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)

        v = QVBoxLayout(card)
        v.setContentsMargins(26, 24, 26, 20)
        v.setSpacing(12)

        title = QLabel("One step left for flash-free capture")
        title.setStyleSheet("font-size: 18px; font-weight: 800; color: #ffffff;")
        title.setWordWrap(True)
        v.addWidget(title)

        body = QLabel(
            "Kapture is ready to use right now — captures work immediately.\n\n"
            "To make them flash-free (and skip GNOME's permission prompt), a small "
            "screenshot helper was installed. GNOME loads it automatically the next "
            "time you log in — then it just works, every session, forever.")
        body.setWordWrap(True)
        body.setStyleSheet("font-size: 13px; color: #b9b9d6;")
        v.addWidget(body)

        hint = QLabel("No rush — keep using Kapture as-is, or log out now to switch "
                      "it on immediately.")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 12px; color: #7a7a99;")
        v.addWidget(hint)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: #23233a; border: none;")
        v.addWidget(line)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        btns.addStretch()
        later = QPushButton("Maybe later")
        later.setCursor(Qt.PointingHandCursor)
        later.setStyleSheet(AnnotationWindow._PILL_BTN_CSS)
        later.clicked.connect(self.reject)
        btns.addWidget(later)
        logout = QPushButton("Log out now")
        logout.setCursor(Qt.PointingHandCursor)
        logout.setStyleSheet(AnnotationWindow._PILL_ACCENT_CSS)
        logout.clicked.connect(self._choose_logout)
        btns.addWidget(logout)
        v.addLayout(btns)

    def _choose_logout(self):
        self.logout = True
        self.accept()

    # Draggable (frameless) ────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPos() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self.move(e.globalPos() - self._drag)
            e.accept()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(e)


# ─── SETTINGS DIALOG ──────────────────────────────────────────────────────────

class SettingsDialog(QDialog):
    """Themed settings — capture shortcut, save folder, and auto-save."""

    _SHORTCUTS = [
        ("Print Screen",          "Print"),
        ("Ctrl + Shift + S",      "<Control><Shift>s"),
        ("Ctrl + Print Screen",   "<Control>Print"),
        ("Super + Shift + S",     "<Super><Shift>s"),
    ]
    _COMBO_CSS = """
        QComboBox {
            background: #15151f; color: #e0e0f4; border: 1px solid #2f2f4d;
            border-radius: 8px; padding: 7px 10px; font-size: 13px;
        }
        QComboBox:hover { border-color: #00aeff; }
        QComboBox::drop-down { border: none; width: 22px; }
        QComboBox QAbstractItemView {
            background: #15151f; color: #e0e0f4; border: 1px solid #2f2f4d;
            selection-background-color: #00aeff; selection-color: #04121d; outline: none;
        }
    """
    _CHECK_CSS = """
        QCheckBox { color: #cfcfe8; font-size: 13px; spacing: 6px; }
        QCheckBox::indicator {
            width: 18px; height: 18px; border-radius: 5px;
            border: 1px solid #2f2f4d; background: #15151f;
        }
        QCheckBox::indicator:checked { background: #00aeff; border-color: #00aeff; }
    """

    def __init__(self, apply_cb=None, parent=None):
        super().__init__(parent)
        self._apply_cb = apply_cb
        self._drag = QPoint()
        self._save_dir = CONFIG.get("save_dir") or os.path.expanduser("~/Pictures")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)

        card = QWidget()
        card.setObjectName("card")
        card.setFixedWidth(440)
        card.setStyleSheet("""
            QWidget#card { background: #0e0e18; border: 1px solid #2a2a44; border-radius: 16px; }
            QLabel { color: #e7e7fb; background: transparent; border: none; }
        """)
        sh = QGraphicsDropShadowEffect(self)
        sh.setBlurRadius(46); sh.setColor(QColor(0, 0, 0, 200)); sh.setOffset(0, 10)
        card.setGraphicsEffect(sh)
        outer.addWidget(card)

        v = QVBoxLayout(card)
        v.setContentsMargins(26, 24, 26, 20)
        v.setSpacing(14)

        title = QLabel("Settings")
        title.setStyleSheet("font-size: 20px; font-weight: 800; color: #ffffff;")
        v.addWidget(title)

        # Capture shortcut
        v.addWidget(self._field_label("Capture shortcut"))
        self.combo = QComboBox()
        self.combo.setCursor(Qt.PointingHandCursor)
        self.combo.setStyleSheet(self._COMBO_CSS)
        for label, _b in self._SHORTCUTS:
            self.combo.addItem(label)
        cur = CONFIG.get("capture_binding", "Print")
        self.combo.setCurrentIndex(
            next((i for i, (_l, b) in enumerate(self._SHORTCUTS) if b == cur), 0))
        v.addWidget(self.combo)

        # Save folder
        v.addWidget(self._field_label("Save folder"))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.dir_lbl = QLabel(self._save_dir)
        self.dir_lbl.setStyleSheet(
            "background: #15151f; border: 1px solid #2f2f4d; border-radius: 8px;"
            "padding: 7px 10px; color: #cfcfe8; font-size: 12px;")
        self.dir_lbl.setMinimumWidth(250)
        row.addWidget(self.dir_lbl, 1)
        browse = QPushButton("Browse")
        browse.setCursor(Qt.PointingHandCursor)
        browse.setStyleSheet(AnnotationWindow._PILL_BTN_CSS)
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        v.addLayout(row)

        # Auto-save
        self.autosave = QCheckBox("  Auto-save (skip the file dialog)")
        self.autosave.setChecked(bool(CONFIG.get("auto_save")))
        self.autosave.setCursor(Qt.PointingHandCursor)
        self.autosave.setStyleSheet(self._CHECK_CSS)
        v.addWidget(self.autosave)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: #23233a; border: none;")
        v.addWidget(line)

        btns = QHBoxLayout()
        btns.addStretch()
        btns.setSpacing(8)
        cancel = QPushButton("Cancel")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setStyleSheet(AnnotationWindow._PILL_BTN_CSS)
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        save = QPushButton("Save")
        save.setCursor(Qt.PointingHandCursor)
        save.setStyleSheet(AnnotationWindow._PILL_ACCENT_CSS)
        save.clicked.connect(self._apply)
        btns.addWidget(save)
        v.addLayout(btns)

    @staticmethod
    def _field_label(text):
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #8a8aa8; font-size: 12px; font-weight: 600;")
        return lbl

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose save folder", self._save_dir)
        if d:
            self._save_dir = d
            self.dir_lbl.setText(d)

    def _apply(self):
        CONFIG["save_dir"] = self._save_dir
        CONFIG["auto_save"] = self.autosave.isChecked()
        binding = self._SHORTCUTS[self.combo.currentIndex()][1]
        CONFIG["capture_binding"] = binding
        save_config(CONFIG)
        if self._apply_cb:
            try:
                self._apply_cb(binding)
            except Exception as e:
                log.debug("SettingsDialog._apply: apply_cb failed: %s", e)
        self.accept()

    # Draggable (frameless) ────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPos() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self.move(e.globalPos() - self._drag)
            e.accept()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(e)


# ─── SYSTEM TRAY ──────────────────────────────────────────────────────────────

class TrayApp(QSystemTrayIcon):
    def __init__(self, app: QApplication):
        log.debug("TrayApp.__init__: initializing")
        # Use a simple colored icon if no icon file
        icon = self._make_icon()
        super().__init__(icon, app)
        self.app = app
        self.engine = ScreenshotEngine()
        self.overlay = None
        self.annotation_win = None
        self._ipc = None

        self._setup_menu()
        self.setToolTip(f"{APP_NAME}\n{_HOTKEY_DISPLAY} to capture")
        self.show()

        # Connect the cross-thread signal
        bridge.trigger_screenshot.connect(self._start_capture)

        # Start global hotkey listener (works on X11; Wayland uses the GNOME
        # shortcut below, which triggers capture over IPC).
        self._start_hotkey_listener()

        # On GNOME Wayland, make sure the flash-free Shell helper is enabled.
        self._ensure_capture_helper()

        # Register Print Screen → Kapture via a GNOME custom shortcut (once).
        self._maybe_setup_shortcuts()

        self._notify_ready()
        log.info("TrayApp.__init__: system tray ready")

    def _make_icon(self) -> QIcon:
        """
        Build the panel/tray icon as a crisp white symbolic 'capture frame' glyph.

        Tray icons sit next to other symbolic indicators (Discord, Caffeine, …) on
        a dark panel, so a flat single-colour silhouette fits far better than the
        full-colour app logo.  Rendered at several sizes so the panel always picks
        a sharp one.  (The colour logo is still used for the launcher and About.)
        """
        log.debug("TrayApp._make_icon: building symbolic tray icon")
        icon = QIcon()
        for s in (16, 18, 22, 24, 32, 48, 64):
            icon.addPixmap(self._render_symbol(s))
        return icon

    @staticmethod
    def _render_symbol(size: int) -> QPixmap:
        """White crop-frame + lens glyph on a transparent background."""
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)

        m = size * 0.18                       # outer margin
        fl, ft, fr, fb = m, m, size - m, size - m
        arm = size * 0.24                     # corner-bracket arm length
        pw = max(1.4, size * 0.09)            # stroke width

        p.setPen(QPen(QColor("#ffffff"), pw, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        for x, y, dx, dy in ((fl, ft, 1, 1), (fr, ft, -1, 1),
                             (fl, fb, 1, -1), (fr, fb, -1, -1)):
            path.moveTo(x + dx * arm, y)
            path.lineTo(x, y)
            path.lineTo(x, y + dy * arm)
        p.drawPath(path)

        # Centre lens dot.
        r = size * 0.085
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QPointF(size / 2.0, size / 2.0), r, r)
        p.end()
        return pm

    def _setup_menu(self):
        log.debug("TrayApp._setup_menu: building tray context menu")
        menu = QMenu()
        # Frameless + translucent so the rounded corners render cleanly.
        menu.setWindowFlags(menu.windowFlags() |
                            Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WA_TranslucentBackground)
        menu.setStyleSheet("""
            QMenu {
                background-color: #0e0e18;
                color: #e7e7fb;
                border: 1px solid #2a2a44;
                border-radius: 11px;
                padding: 6px;
            }
            QMenu::item {
                padding: 9px 22px 9px 14px;
                margin: 2px 4px;
                border-radius: 7px;
                font-size: 13px;
            }
            QMenu::item:selected {
                background-color: #00aeff;
                color: #04121d;
            }
            QMenu::separator {
                height: 1px;
                background: #23233a;
                margin: 6px 10px;
            }
        """)

        capture_action = QAction(f"📷  Capture Region  ({_HOTKEY_DISPLAY})", self)
        capture_action.triggered.connect(self._start_capture)

        settings_action = QAction("⚙️  Settings", self)
        settings_action.triggered.connect(self._show_settings)

        shortcut_action = QAction("⌨️  Set capture shortcut", self)
        shortcut_action.triggered.connect(self._setup_shortcuts_interactive)

        about_action = QAction(f"ℹ️  About {APP_NAME}", self)
        about_action.triggered.connect(self._show_about)

        quit_action = QAction("✕  Quit", self)
        quit_action.triggered.connect(self.app.quit)

        menu.addAction(capture_action)
        menu.addSeparator()
        menu.addAction(settings_action)
        menu.addAction(shortcut_action)
        menu.addSeparator()
        menu.addAction(about_action)
        menu.addAction(quit_action)

        self.setContextMenu(menu)
        self.activated.connect(self._on_tray_activated)
        log.debug("TrayApp._setup_menu: menu ready")

    def _on_tray_activated(self, reason):
        log.debug("TrayApp._on_tray_activated: reason=%s", reason)
        if reason == QSystemTrayIcon.DoubleClick:
            log.info("TrayApp._on_tray_activated: double-click, starting capture")
            self._start_capture()

    def _start_capture(self):
        if self.engine.busy:
            log.info("TrayApp._start_capture: capture already in flight, ignoring")
            return
        log.info("TrayApp._start_capture: scheduling capture (%dms delay)", CAPTURE_DELAY_MS)
        # Hide the tray menu/notification before grabbing so they don't appear in
        # the shot; a short delay is all that's needed (was an unconditional 500ms).
        QTimer.singleShot(CAPTURE_DELAY_MS, lambda: asyncio.ensure_future(self._do_capture()))

    async def _do_capture(self):
        log.info("TrayApp._do_capture: capturing full screen")
        result = await self.engine.capture_full_screen()
        if result is None or result.pixmap.isNull():
            log.error("TrayApp._do_capture: capture failed")
            self._show_capture_error()
            return
        log.debug("TrayApp._do_capture: opening overlay")
        self.overlay = OverlayWindow(result)
        self.overlay.screenshot_taken.connect(self._on_region_selected)

    def _show_capture_error(self):
        """Shown only when a capture genuinely fails — never a silent black PNG."""
        log.warning("TrayApp._show_capture_error: notifying user")
        dlg = QMessageBox()
        dlg.setWindowTitle(f"{APP_NAME} — Capture Failed")
        dlg.setIcon(QMessageBox.Warning)
        dlg.setTextFormat(Qt.RichText)
        dlg.setText(
            "<b>Couldn't capture the screen.</b><br><br>"
            "Kapture captures natively (no external tools needed). On X11 this "
            "always works; on Wayland it uses the desktop portal.<br><br>"
            "If you're on a Wayland session where the portal is unavailable, "
            "either log in with an <b>Xorg</b> session, or on a wlroots compositor "
            "(Sway/Hyprland) install <code>grim</code>:"
        )
        dlg.setDetailedText(
            "Wayland portal backend (present by default on Ubuntu GNOME/KDE):\n"
            "  sudo apt install xdg-desktop-portal-gnome\n\n"
            "wlroots compositors (Sway, Hyprland):\n"
            "  sudo apt install grim\n\n"
            "Or pick 'Ubuntu on Xorg' from the login screen gear menu."
        )
        dlg.setStyleSheet("""
            QMessageBox { background-color: #0d0d1a; color: #e0e0ff; }
            QLabel { color: #e0e0ff; font-size: 13px; }
            QPushButton {
                background: #1a1a2e; color: #e0e0ff;
                border: 1px solid #00aeff55; border-radius: 5px;
                padding: 4px 16px;
            }
            QPushButton:hover { background: #00aeff22; border-color: #00aeff; }
            QTextEdit {
                background: #11112a; color: #a0c4ff;
                border: 1px solid #00aeff33; border-radius: 4px;
                font-family: monospace; font-size: 12px;
            }
        """)
        dlg.setStandardButtons(QMessageBox.Ok)
        dlg.exec_()

    def _ensure_capture_helper(self):
        """
        On GNOME Wayland, ensure Kapture's flash-free Shell extension is enabled.

        The .deb installs the extension system-wide, but GNOME loads/enables it
        per user session.  If the helper's D-Bus service isn't up yet but the
        extension is installed, enable it and (once) tell the user to log out/in
        so the Shell picks it up.  Until then capture falls back to the portal.
        """
        if not ScreenshotEngine._session_is_wayland():
            return
        if "gnome" not in os.environ.get("XDG_CURRENT_DESKTOP", "").lower():
            return

        bus = QDBusConnection.sessionBus()
        iface = QDBusInterface(_KAP_EXT_SERVICE, _KAP_EXT_PATH, _KAP_EXT_IFACE, bus)
        if iface.isValid():
            log.info("_ensure_capture_helper: flash-free Shell helper is active")
            return

        installed = any(
            os.path.isfile(os.path.join(d, _KAP_EXT_UUID, "metadata.json"))
            for d in ("/usr/share/gnome-shell/extensions",
                      os.path.expanduser("~/.local/share/gnome-shell/extensions"))
        )
        if not installed:
            log.debug("_ensure_capture_helper: extension not installed; using portal")
            return

        # Pre-stage the extension in `enabled-extensions` so GNOME auto-enables
        # it on the next login.  `gnome-extensions enable` won't work yet because
        # the Shell hasn't scanned a freshly-installed system extension, so we
        # edit the gsetting array directly (and also try the CLI as a no-op-safe
        # bonus for the already-scanned case).
        self._register_extension_for_next_login()
        if shutil.which("gnome-extensions"):
            try:
                subprocess.run(["gnome-extensions", "enable", _KAP_EXT_UUID],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=5)
            except Exception:
                pass

        # Offer a ONE-TIME, optional activation prompt — never a forced relogin.
        # The app already works via the portal; this just lets eager users switch
        # on the flash-free helper immediately instead of at their next login.
        stamp = os.path.expanduser("~/.cache/kapture/helper_notice")
        if not os.path.exists(stamp):
            try:
                os.makedirs(os.path.dirname(stamp), exist_ok=True)
                open(stamp, "w").close()
            except OSError:
                pass
            # Defer so the tray icon appears first; dialog is modal.
            QTimer.singleShot(1500, self._offer_helper_activation)

    def _offer_helper_activation(self):
        log.debug("TrayApp._offer_helper_activation: showing one-time setup prompt")
        dlg = HelperSetupDialog()
        dlg.exec_()
        if dlg.logout:
            self._logout_session()

    def _logout_session(self):
        """Politely request a session logout (the OS shows its own confirmation,
        so the user can still save work / cancel)."""
        log.info("TrayApp._logout_session: requesting logout to activate helper")
        if shutil.which("gnome-session-quit"):
            try:
                subprocess.Popen(["gnome-session-quit", "--logout"])
                return
            except Exception as e:
                log.debug("_logout_session: gnome-session-quit failed: %s", e)
        try:  # D-Bus fallback; mode 0 = normal logout (shows confirmation)
            iface = QDBusInterface("org.gnome.SessionManager",
                                   "/org/gnome/SessionManager",
                                   "org.gnome.SessionManager",
                                   QDBusConnection.sessionBus())
            iface.call("Logout", 0)
        except Exception as e:
            log.debug("_logout_session: SessionManager logout failed: %s", e)

    @staticmethod
    def _register_extension_for_next_login():
        """Append the extension UUID to org.gnome.shell enabled-extensions so the
        Shell auto-enables it at the next login (works even before the Shell has
        scanned the freshly-installed system extension)."""
        if not shutil.which("gsettings"):
            return
        try:
            out = subprocess.run(
                ["gsettings", "get", "org.gnome.shell", "enabled-extensions"],
                capture_output=True, text=True, timeout=5)
            raw = out.stdout.strip()
            if _KAP_EXT_UUID in raw:
                return                          # already registered
            if raw.startswith("@as"):           # empty array prints as "@as []"
                raw = raw[raw.find("["):]
            try:
                import ast
                current = ast.literal_eval(raw) if raw.startswith("[") else []
            except (ValueError, SyntaxError):
                current = []
            current.append(_KAP_EXT_UUID)
            new = "[" + ", ".join("'%s'" % u for u in current) + "]"
            subprocess.run(["gsettings", "set", "org.gnome.shell",
                            "enabled-extensions", new], timeout=5)
            log.info("_register_extension_for_next_login: registered %s", _KAP_EXT_UUID)
        except Exception as e:
            log.debug("_register_extension_for_next_login: %s", e)

    def _on_region_selected(self, cropped: QPixmap, region: QRect):
        log.info("TrayApp._on_region_selected: region %dx%d at (%d,%d)",
                 region.width(), region.height(), region.x(), region.y())
        self.annotation_win = AnnotationWindow(cropped, region)

    def _start_hotkey_listener(self):
        log.debug("TrayApp._start_hotkey_listener: registering hotkeys %s", HOTKEYS)

        def on_activate():
            log.info("TrayApp._start_hotkey_listener: hotkey fired")
            bridge.trigger_screenshot.emit()

        hotkeys = [
            keyboard.HotKey(keyboard.HotKey.parse(hk), on_activate)
            for hk in HOTKEYS
        ]

        def on_press(k):
            k = listener.canonical(k)
            for hk in hotkeys:
                hk.press(k)

        def on_release(k):
            k = listener.canonical(k)
            for hk in hotkeys:
                hk.release(k)

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.daemon = True
        listener.start()
        log.info("TrayApp._start_hotkey_listener: listener started")

    # ── single-instance IPC (lets `kapture --capture` trigger this instance) ──

    def start_ipc_server(self):
        """Listen on a per-user local socket so a `kapture --capture` launched by
        the GNOME shortcut triggers THIS running instance instead of spawning a
        duplicate tray app."""
        try:
            QLocalServer.removeServer(_IPC_NAME)   # clear any stale socket
            self._ipc = QLocalServer(self)
            if self._ipc.listen(_IPC_NAME):
                self._ipc.newConnection.connect(self._on_ipc_connection)
                log.info("TrayApp.start_ipc_server: listening on %s", _IPC_NAME)
            else:
                log.warning("TrayApp.start_ipc_server: listen failed: %s",
                            self._ipc.errorString())
        except Exception as e:
            log.warning("TrayApp.start_ipc_server: %s", e)

    def _on_ipc_connection(self):
        conn = self._ipc.nextPendingConnection()
        if conn is None:
            return

        def _read():
            data = bytes(conn.readAll()).decode("utf-8", "ignore")
            log.info("TrayApp._on_ipc_connection: received %r", data)
            if "capture" in data:
                bridge.trigger_screenshot.emit()
            conn.disconnectFromServer()

        conn.readyRead.connect(_read)

    # ── GNOME custom shortcut (Print Screen → Kapture) ────────────────────────

    @staticmethod
    def _launch_command() -> str:
        """Shell command GNOME runs for the shortcut (paths quoted for spaces)."""
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}" --capture'
        return f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}" --capture'

    def _setup_gnome_shortcuts(self) -> bool:
        """Bind Print Screen (and Ctrl+Shift+S) to Kapture via a GNOME custom
        keybinding, and free Print from GNOME's built-in screenshot UI.
        Returns True on a GNOME session where the bindings were applied."""
        if "gnome" not in os.environ.get("XDG_CURRENT_DESKTOP", "").lower():
            log.debug("_setup_gnome_shortcuts: not GNOME; skipping")
            return False
        if not shutil.which("gsettings"):
            return False
        cmd = self._launch_command()
        binding = CONFIG.get("capture_binding", "Print")
        try:
            self._register_custom_keybinding("kapture", "Kapture — Capture", cmd, binding)
            if binding == "Print":
                self._free_gnome_print()
            log.info("_setup_gnome_shortcuts: %s now launches Kapture", binding)
            return True
        except Exception as e:
            log.warning("_setup_gnome_shortcuts: %s", e)
            return False

    @staticmethod
    def _gs_get(args):
        return subprocess.run(["gsettings", "get"] + args,
                              capture_output=True, text=True, timeout=5).stdout.strip()

    @staticmethod
    def _gs_set(args):
        subprocess.run(["gsettings", "set"] + args, timeout=5)

    @classmethod
    def _register_custom_keybinding(cls, slug, name, command, binding):
        import ast
        base = "org.gnome.settings-daemon.plugins.media-keys"
        path = ("/org/gnome/settings-daemon/plugins/media-keys/"
                f"custom-keybindings/{slug}/")
        raw = cls._gs_get([base, "custom-keybindings"])
        if raw.startswith("@as"):
            raw = raw[raw.find("["):]
        try:
            paths = ast.literal_eval(raw) if raw.startswith("[") else []
        except (ValueError, SyntaxError):
            paths = []
        if path not in paths:
            paths.append(path)
            cls._gs_set([base, "custom-keybindings",
                         "[" + ", ".join("'%s'" % p for p in paths) + "]"])
        schema = ("org.gnome.settings-daemon.plugins.media-keys."
                  "custom-keybinding:" + path)
        cls._gs_set([schema, "name", name])
        cls._gs_set([schema, "command", command])
        cls._gs_set([schema, "binding", binding])

    # GNOME's own screenshot accelerators that can collide with `Print`.
    _GNOME_SS_KEYS = ("show-screenshot-ui", "screenshot", "screenshot-window")

    @staticmethod
    def _parse_gs_list(raw):
        """Parse a gsettings 'as' value ('@as []', "['Print']") into a list."""
        import ast
        raw = (raw or "").strip()
        if raw.startswith("@as"):
            raw = raw[raw.find("["):]
        try:
            return list(ast.literal_eval(raw)) if raw.startswith("[") else []
        except (ValueError, SyntaxError):
            return []

    @staticmethod
    def _fmt_gs_list(vals):
        return "[" + ", ".join("'%s'" % v for v in vals) + "]"

    @classmethod
    def _backup_gnome_screenshot(cls):
        """Save GNOME's current screenshot keybindings once, before we touch them,
        so they can be restored later. Never overwrites an existing backup (that
        would capture our own already-emptied values)."""
        if os.path.exists(_GNOME_SS_BACKUP):
            return
        import json
        snapshot = {}
        for key in cls._GNOME_SS_KEYS:
            try:
                snapshot[key] = cls._parse_gs_list(
                    cls._gs_get(["org.gnome.shell.keybindings", key]))
            except Exception as e:
                log.debug("_backup_gnome_screenshot: %s (%s)", key, e)
        try:
            os.makedirs(os.path.dirname(_GNOME_SS_BACKUP), exist_ok=True)
            with open(_GNOME_SS_BACKUP, "w") as f:
                json.dump(snapshot, f, indent=2)
            log.info("_backup_gnome_screenshot: saved %s", snapshot)
        except OSError as e:
            log.debug("_backup_gnome_screenshot: %s", e)

    @classmethod
    def _free_gnome_print(cls):
        """Remove 'Print' from GNOME's own screenshot keybindings so our custom
        binding takes effect (GNOME 42+ binds the screenshot UI to Print).
        Backs the originals up first so they can be restored."""
        cls._backup_gnome_screenshot()
        for key in cls._GNOME_SS_KEYS:
            try:
                vals = cls._parse_gs_list(
                    cls._gs_get(["org.gnome.shell.keybindings", key]))
                if "Print" not in vals:
                    continue
                vals = [v for v in vals if v != "Print"]
                cls._gs_set(["org.gnome.shell.keybindings", key,
                             cls._fmt_gs_list(vals)])
                log.info("_free_gnome_print: removed Print from %s", key)
            except Exception as e:
                log.debug("_free_gnome_print: %s (%s)", key, e)

    @classmethod
    def _restore_gnome_screenshot(cls):
        """Give GNOME its screenshot keybindings back. Restores the saved backup
        if present, otherwise falls back to GNOME's factory defaults. Called when
        Kapture stops owning `Print` (hotkey changed away, or on uninstall)."""
        import json
        # GNOME factory defaults — the safety net when no backup exists.
        defaults = {
            "show-screenshot-ui": ["Print"],
            "screenshot": ["<Shift>Print"],
            "screenshot-window": ["<Alt>Print"],
        }
        snapshot = None
        try:
            with open(_GNOME_SS_BACKUP) as f:
                snapshot = json.load(f)
        except (OSError, ValueError):
            snapshot = None
        restore = snapshot if isinstance(snapshot, dict) and snapshot else defaults
        for key, vals in restore.items():
            try:
                cls._gs_set(["org.gnome.shell.keybindings", key,
                             cls._fmt_gs_list(vals)])
                log.info("_restore_gnome_screenshot: %s -> %s", key, vals)
            except Exception as e:
                log.debug("_restore_gnome_screenshot: %s (%s)", key, e)
        try:
            os.remove(_GNOME_SS_BACKUP)
        except OSError:
            pass

    def _maybe_setup_shortcuts(self):
        """Run the GNOME shortcut setup once per machine (stamped)."""
        stamp = os.path.expanduser("~/.cache/kapture/shortcuts_set")
        if os.path.exists(stamp):
            return
        if self._setup_gnome_shortcuts():
            self.showMessage(
                APP_NAME,
                "Print Screen now opens Kapture. (Change it anytime in "
                "Settings → Keyboard → Shortcuts.)",
                QSystemTrayIcon.Information, 6000)
        try:
            os.makedirs(os.path.dirname(stamp), exist_ok=True)
            open(stamp, "w").close()
        except OSError:
            pass

    def _setup_shortcuts_interactive(self):
        ok = self._setup_gnome_shortcuts()
        self.showMessage(
            APP_NAME,
            "Print Screen now opens Kapture." if ok else
            "Couldn't set it automatically — add a shortcut in Settings → "
            "Keyboard pointing to:  kapture --capture",
            QSystemTrayIcon.Information, 6000)

    def _notify_ready(self):
        log.info("TrayApp._notify_ready: showing startup notification")
        self.showMessage(
            APP_NAME,
            f"Running in background.\nPress {_HOTKEY_DISPLAY} to capture a region.",
            QSystemTrayIcon.Information,
            3000
        )

    def _show_about(self):
        log.debug("TrayApp._show_about: opening about dialog")
        AboutDialog().exec_()

    def _show_settings(self):
        log.debug("TrayApp._show_settings: opening settings dialog")
        SettingsDialog(apply_cb=self._apply_capture_binding).exec_()

    def _apply_capture_binding(self, binding: str):
        """Re-bind the GNOME capture shortcut to the chosen accelerator."""
        if "gnome" not in os.environ.get("XDG_CURRENT_DESKTOP", "").lower():
            return
        if not shutil.which("gsettings"):
            return
        try:
            self._register_custom_keybinding(
                "kapture", "Kapture — Capture", self._launch_command(), binding)
            if binding == "Print":
                self._free_gnome_print()
            else:
                # We no longer own Print — hand GNOME's screenshot keys back.
                self._restore_gnome_screenshot()
            log.info("TrayApp._apply_capture_binding: %s", binding)
        except Exception as e:
            log.warning("TrayApp._apply_capture_binding: %s", e)


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

def main():
    log.info("main: starting %s", APP_NAME)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)  # Stay alive in tray

    want_capture = "--capture" in sys.argv[1:]

    # ── Single instance: if Kapture is already running, forward the request ────
    probe = QLocalSocket()
    probe.connectToServer(_IPC_NAME)
    if probe.waitForConnected(300):
        probe.write(b"capture" if want_capture else b"show")
        probe.flush()
        probe.waitForBytesWritten(500)
        probe.disconnectFromServer()
        log.info("main: forwarded '%s' to the running instance; exiting",
                 "capture" if want_capture else "show")
        return

    # Set application-wide window icon (taskbar, alt-tab, dialogs)
    icon_file = resource_path(os.path.join('assets', 'app-logo.png'))
    if os.path.isfile(icon_file):
        app.setWindowIcon(QIcon(icon_file))
        log.debug("main: app icon set from %s", icon_file)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.critical("main: system tray not available")
        QMessageBox.critical(None, APP_NAME, "System tray not available on this desktop.")
        sys.exit(1)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    log.info("main: asyncio event loop set to qasync.QEventLoop")

    tray = TrayApp(app)
    tray.start_ipc_server()

    # If launched as `kapture --capture` while not already running, capture once.
    if want_capture:
        QTimer.singleShot(600, lambda: bridge.trigger_screenshot.emit())

    with loop:
        log.info("main: entering event loop")
        loop.run_forever()


if __name__ == "__main__":
    main()
