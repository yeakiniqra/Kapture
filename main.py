#!/usr/bin/env python3
"""
Kapture - A Lightshot-like screenshot tool for Ubuntu
Features: region selection, annotation toolbar, copy & save
"""

import sys
import os
import time
import math
import asyncio
import logging
import tempfile
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QHBoxLayout, QVBoxLayout,
    QSystemTrayIcon, QMenu, QAction, QMessageBox, QFileDialog,
    QColorDialog, QRubberBand, QToolTip, QSizePolicy
)
from PyQt5.QtGui import (
    QPixmap, QColor, QPainter, QPen, QBrush, QFont,
    QCursor, QIcon, QGuiApplication, QScreen, QClipboard, QPolygon
)
from PyQt5.QtCore import (
    Qt, QRect, QPoint, QSize, QTimer, pyqtSignal, QObject
)
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

HOTKEY = "<ctrl>+<shift>+s"               # Change to your preferred hotkey
APP_NAME = "Kapture"


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

    def __init__(self, full_pixmap: QPixmap):
        log.debug("OverlayWindow.__init__: initializing")
        super().__init__()
        self.full_pixmap = full_pixmap
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

        # Cover all screens
        screen_geo = QApplication.primaryScreen().virtualGeometry()
        self.setGeometry(screen_geo)
        self.showFullScreen()
        log.debug("OverlayWindow._setup_ui: shown at %s", screen_geo)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Draw the captured screen as base
        painter.drawPixmap(0, 0, self.full_pixmap)

        # Darken overlay
        painter.fillRect(self.rect(), QColor(0, 0, 0, 120))

        if not self.selection.isNull() and self.is_drawing:
            sel = self.selection.normalized()

            # Cut out the selection (bright, not darkened)
            painter.drawPixmap(sel, self.full_pixmap, sel)

            # Selection border
            pen = QPen(QColor(0, 174, 255), 2, Qt.SolidLine)
            painter.setPen(pen)
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
                cropped = self.full_pixmap.copy(sel)
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

    TOOL_PEN   = 'pen'
    TOOL_ARROW = 'arrow'
    TOOL_RECT  = 'rect'
    TOOLBAR_H  = 46

    _last_pos: QPoint = None

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
        self.setStyleSheet("QWidget { background: #0d0d1a; border: 1px solid #00aeff55; }")

        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        # Canvas
        self.canvas = _AnnotationCanvas(self)
        root.addWidget(self.canvas)

        # Toolbar (also the drag handle)
        tb_widget = _DragHandle(self)
        tb_widget.setFixedHeight(self.TOOLBAR_H)
        tb_widget.setStyleSheet(
            "background-color: #0d0d1a; border-top: 1px solid #00aeff44;"
        )
        tb = QHBoxLayout(tb_widget)
        tb.setContentsMargins(8, 6, 8, 6)
        tb.setSpacing(5)

        tool_css = """
            QPushButton {
                background: #1a1a2e; color: #e0e0ff;
                border: 1px solid #00aeff33; border-radius: 5px;
                font-size: 15px;
                min-width: 30px; max-width: 30px;
                min-height: 30px; max-height: 30px;
            }
            QPushButton:hover  { background: #00aeff22; border-color: #00aeff; }
            QPushButton:checked { background: #00aeff55; border-color: #00aeff; color: #fff; }
        """
        for icon, tool in [("✏", self.TOOL_PEN), ("↗", self.TOOL_ARROW), ("▭", self.TOOL_RECT)]:
            btn = QPushButton(icon)
            btn.setCheckable(True)
            btn.setChecked(tool == self.tool)
            btn.setStyleSheet(tool_css)
            btn.setToolTip({"pen": "Freehand pen", "arrow": "Arrow", "rect": "Rectangle"}[tool])
            btn.clicked.connect(lambda _, t=tool: self._select_tool(t))
            self._tool_btns[tool] = btn
            tb.addWidget(btn)

        # Color swatch
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(30, 30)
        self.color_btn.setToolTip("Pick color")
        self.color_btn.clicked.connect(self._pick_color)
        self._refresh_color_btn()
        tb.addWidget(self.color_btn)

        tb.addStretch()

        action_css = """
            QPushButton {
                background: #1a1a2e; color: #e0e0ff;
                border: 1px solid #00aeff44; border-radius: 5px;
                padding: 3px 12px; font-size: 12px;
            }
            QPushButton:hover { background: #00aeff22; border-color: #00aeff; color: #fff; }
        """
        save_btn = QPushButton("⬇  Save")
        save_btn.setStyleSheet(action_css)
        save_btn.setToolTip("Save with annotations")
        save_btn.clicked.connect(lambda: asyncio.ensure_future(self._save_to_file()))
        tb.addWidget(save_btn)

        close_btn = QPushButton("✕")
        close_btn.setStyleSheet("""
            QPushButton { background: transparent; border: none;
                          color: #555577; font-size: 14px;
                          min-width: 24px; max-width: 24px; }
            QPushButton:hover { color: #ff4466; }
        """)
        close_btn.clicked.connect(self.close)
        tb.addWidget(close_btn)

        root.addWidget(tb_widget)
        self.adjustSize()
        log.debug("AnnotationWindow._setup_ui: done, size=%s", self.size())

    def _select_tool(self, tool: str):
        log.debug("AnnotationWindow._select_tool: %s", tool)
        self.tool = tool
        for t, b in self._tool_btns.items():
            b.setChecked(t == tool)

    def _refresh_color_btn(self):
        self.color_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.color.name()};
                border: 2px solid #00aeff55; border-radius: 5px;
            }}
            QPushButton:hover {{ border-color: #00aeff; }}
        """)

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
            screen = QApplication.primaryScreen().geometry()
            x = max(0, min(self.region.x(), screen.right() - self.width() - 4))
            y = max(0, min(self.region.y(), screen.bottom() - self.height() - 4))
            self.move(x, y)
        self.show()
        self.raise_()
        log.debug("AnnotationWindow._position_window: shown at %s", self.pos())

    # ── save ──────────────────────────────────────────────────────────────────

    async def _save_to_file(self):
        log.info("AnnotationWindow._save_to_file: opening dialog")
        ts = time.strftime("%Y%m%d_%H%M%S")
        default = os.path.expanduser(f"~/Pictures/kapture_{ts}.png")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Screenshot", default,
            "PNG Image (*.png);;JPEG Image (*.jpg);;All Files (*)"
        )
        if path:
            log.info("AnnotationWindow._save_to_file: saving to %s", path)
            final = self._final_pixmap()
            await asyncio.to_thread(final.save, path)
            QToolTip.showText(self.mapToGlobal(QPoint(0, -30)),
                              f"Saved: {os.path.basename(path)}", self)
            log.info("AnnotationWindow._save_to_file: done")
        else:
            log.debug("AnnotationWindow._save_to_file: cancelled")


# ─── CAPTURE ENGINE ───────────────────────────────────────────────────────────

class ScreenshotEngine:
    """Grabs a full virtual desktop screenshot."""

    @staticmethod
    async def _try_tool(name: str, *cmd_args, tmpfile: str) -> bool:
        """Run an external capture tool and return True if it produced a file."""
        try:
            log.debug("ScreenshotEngine._try_tool: trying %s", name)
            proc = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            if proc.returncode == 0 and os.path.getsize(tmpfile) > 0:
                log.debug("ScreenshotEngine._try_tool: %s succeeded", name)
                return True
            log.warning("ScreenshotEngine._try_tool: %s exited with code %d", name, proc.returncode)
        except FileNotFoundError:
            log.warning("ScreenshotEngine._try_tool: %s not found", name)
        except Exception as e:
            log.warning("ScreenshotEngine._try_tool: %s failed: %s", name, e)
        return False

    @staticmethod
    async def capture_full_screen() -> QPixmap:
        log.debug("ScreenshotEngine.capture_full_screen: starting")

        # Create a temp file that external tools will overwrite
        fd, tmpfile = tempfile.mkstemp(suffix='.png')
        os.close(fd)

        try:
            # Method 1: gnome-screenshot (works correctly on GNOME Wayland via XDG portal)
            success = await ScreenshotEngine._try_tool(
                'gnome-screenshot', 'gnome-screenshot', '-f', tmpfile, tmpfile=tmpfile
            )

            # Method 2: scrot (X11 / composited X11 fallback)
            if not success:
                success = await ScreenshotEngine._try_tool(
                    'scrot', 'scrot', tmpfile, tmpfile=tmpfile
                )

            if success:
                pixmap = QPixmap(tmpfile)
                if not pixmap.isNull():
                    log.info("ScreenshotEngine.capture_full_screen: captured %dx%d",
                             pixmap.width(), pixmap.height())
                    return pixmap
                log.warning("ScreenshotEngine.capture_full_screen: tool succeeded but pixmap is null")

            # Final fallback: Qt grabWindow (works on plain X11 without compositor)
            log.debug("ScreenshotEngine.capture_full_screen: using Qt grabWindow fallback")
            screen = QApplication.primaryScreen()
            virtual_geo = screen.virtualGeometry()
            pixmap = screen.grabWindow(
                0,
                virtual_geo.x(), virtual_geo.y(),
                virtual_geo.width(), virtual_geo.height()
            )
            log.info("ScreenshotEngine.capture_full_screen: captured %dx%d via Qt grabWindow",
                     pixmap.width(), pixmap.height())
            return pixmap

        finally:
            try:
                os.unlink(tmpfile)
            except FileNotFoundError:
                pass


# ─── SYSTEM TRAY ──────────────────────────────────────────────────────────────

class TrayApp(QSystemTrayIcon):
    def __init__(self, app: QApplication):
        log.debug("TrayApp.__init__: initializing")
        # Use a simple colored icon if no icon file
        icon = self._make_icon()
        super().__init__(icon, app)
        self.app = app
        self.overlay = None
        self.annotation_win = None

        self._setup_menu()
        self.setToolTip(f"{APP_NAME}\n{HOTKEY} to capture")
        self.show()

        # Connect the cross-thread signal
        bridge.trigger_screenshot.connect(self._start_capture)

        # Start global hotkey listener
        self._start_hotkey_listener()

        self._notify_ready()
        log.info("TrayApp.__init__: system tray ready")

    def _make_icon(self) -> QIcon:
        log.debug("TrayApp._make_icon: building programmatic icon")
        """Create a simple camera-style icon programmatically."""
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QBrush(QColor(0, 174, 255)))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(2, 6, 28, 22, 4, 4)
        p.setBrush(QBrush(QColor(13, 13, 26)))
        p.drawEllipse(10, 10, 12, 12)
        p.setBrush(QBrush(QColor(0, 174, 255, 180)))
        p.drawEllipse(13, 13, 6, 6)
        p.end()
        log.debug("TrayApp._make_icon: icon created")
        return QIcon(pixmap)

    def _setup_menu(self):
        log.debug("TrayApp._setup_menu: building tray context menu")
        menu = QMenu()
        menu.setStyleSheet("""
            QMenu {
                background-color: #0d0d1a;
                color: #e0e0ff;
                border: 1px solid #00aeff33;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item:selected {
                background-color: #00aeff22;
            }
            QMenu::separator {
                height: 1px;
                background: #00aeff22;
                margin: 4px 8px;
            }
        """)

        capture_action = QAction(f"📷  Capture Region  ({HOTKEY})", self)
        capture_action.triggered.connect(self._start_capture)

        about_action = QAction(f"ℹ️  About {APP_NAME}", self)
        about_action.triggered.connect(self._show_about)

        quit_action = QAction("✕  Quit", self)
        quit_action.triggered.connect(self.app.quit)

        menu.addAction(capture_action)
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
        log.info("TrayApp._start_capture: scheduling capture (500ms delay)")
        QTimer.singleShot(500, lambda: asyncio.ensure_future(self._do_capture()))

    async def _do_capture(self):
        log.info("TrayApp._do_capture: capturing full screen")
        pixmap = await ScreenshotEngine.capture_full_screen()
        if pixmap.isNull():
            log.error("TrayApp._do_capture: capture returned null pixmap")
            return
        log.debug("TrayApp._do_capture: opening overlay")
        self.overlay = OverlayWindow(pixmap)
        self.overlay.screenshot_taken.connect(self._on_region_selected)

    def _on_region_selected(self, cropped: QPixmap, region: QRect):
        log.info("TrayApp._on_region_selected: region %dx%d at (%d,%d)",
                 region.width(), region.height(), region.x(), region.y())
        self.annotation_win = AnnotationWindow(cropped, region)

    def _start_hotkey_listener(self):
        log.debug("TrayApp._start_hotkey_listener: registering hotkey '%s'", HOTKEY)
        def on_activate():
            log.info("TrayApp._start_hotkey_listener: hotkey fired")
            bridge.trigger_screenshot.emit()

        def parse_hotkey(hk_str):
            return keyboard.HotKey(
                keyboard.HotKey.parse(hk_str),
                on_activate
            )

        hotkey = parse_hotkey(HOTKEY)

        def for_canonical(f):
            return lambda k: f(listener.canonical(k))

        listener = keyboard.Listener(
            on_press=for_canonical(hotkey.press),
            on_release=for_canonical(hotkey.release)
        )
        listener.daemon = True
        listener.start()
        log.info("TrayApp._start_hotkey_listener: listener started")

    def _notify_ready(self):
        log.info("TrayApp._notify_ready: showing startup notification")
        self.showMessage(
            APP_NAME,
            f"Running in background.\nPress {HOTKEY} to capture a region.",
            QSystemTrayIcon.Information,
            3000
        )

    def _show_about(self):
        log.debug("TrayApp._show_about: opening about dialog")
        QMessageBox.about(
            None, f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> — Lightshot-style screenshot tool for Ubuntu<br><br>"
            f"Hotkey: <code>{HOTKEY}</code><br><br>"
            f"Built with PyQt5 + pynput"
        )


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

def main():
    log.info("main: starting %s", APP_NAME)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)  # Stay alive in tray

    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.critical("main: system tray not available")
        QMessageBox.critical(None, APP_NAME, "System tray not available on this desktop.")
        sys.exit(1)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    log.info("main: asyncio event loop set to qasync.QEventLoop")

    tray = TrayApp(app)

    with loop:
        log.info("main: entering event loop")
        loop.run_forever()


if __name__ == "__main__":
    main()
