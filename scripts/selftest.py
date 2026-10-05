#!/usr/bin/env python3
"""Headless regression check for the UI logic — no display, no real capture.

    venv/bin/python scripts/selftest.py

Runs on a fake dual-monitor HiDPI setup (Qt offscreen platform) and exits
non-zero on the first failure. Never touches the user's config or GNOME.
"""

import json
import os
import random
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_cfg = os.path.join(tempfile.gettempdir(), "kapture-selftest-screens.json")
with open(_cfg, "w") as f:                       # two 1920x1080 monitors side by side
    json.dump({"screens": [
        {"name": "L", "x": 0, "y": 0, "width": 1920, "height": 1080, "logicalDpi": 96,
         "logicalBaseDpi": 96, "dpr": 1},
        {"name": "R", "x": 1920, "y": 0, "width": 1920, "height": 1080, "logicalDpi": 96,
         "logicalBaseDpi": 96, "dpr": 1}]}, f)
os.environ["QT_QPA_PLATFORM"] = f"offscreen:configfile={_cfg}"
os.environ["QT_API"] = "pyside6"
sys.path.insert(0, ROOT)

from PySide6.QtCore import QPoint, QRect  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPixmap  # noqa: E402
from PySide6.QtNetwork import QLocalSocket  # noqa: E402
from PySide6.QtWidgets import QApplication, QSystemTrayIcon  # noqa: E402
from shiboken6 import isValid  # noqa: E402

app = QApplication(sys.argv)

import kapture.config as config  # noqa: E402
import kapture.ui.editor as editor  # noqa: E402
from kapture import theme  # noqa: E402
from kapture.capture import CaptureResult  # noqa: E402
from kapture.ui.overlay import OverlayWindow  # noqa: E402

editor.save_config = config.save_config = lambda cfg: None     # hands off real config
theme.load_fonts()


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        sys.exit(1)


def noise(w, h, dpr=1.0) -> QPixmap:
    img = QImage(int(w * dpr), int(h * dpr), QImage.Format.Format_RGB32)
    random.seed(7)
    for y in range(img.height()):
        for x in range(img.width()):
            img.setPixelColor(x, y, QColor(random.randrange(256), 40, 90))
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm


def drag(ed, tool, *pts):
    ed.select_tool(tool)
    ed.press(QPoint(*pts[0]))
    for q in pts[1:]:
        ed.drag_to(QPoint(*q))
    ed.release(QPoint(*pts[-1]))


# ── editor: every tool, HiDPI ────────────────────────────────────────────────
src = noise(200, 100, dpr=2.0)
ed = editor.AnnotationWindow(src, QRect(100, 100, 200, 100))
before = ed.final().toImage()
for tool, pts in (("pen", [(5, 5), (40, 30), (80, 10)]), ("marker", [(10, 60), (120, 60)]),
                  ("arrow", [(20, 80), (90, 40)]), ("rect", [(100, 10), (150, 50)]),
                  ("step", [(170, 20), (170, 20)])):
    n = len(ed._undo)
    drag(ed, tool, *pts)
    check(len(ed._undo) == n + 1, f"{tool} commits one undo step")
ed.select_tool("text")
ed.press(QPoint(30, 40))
ed._text.setText("hi")
ed.commit_text()
check(ed._text is None and len(ed._undo) == 6, "text commits and closes its editor")
check(ed.step == 2, "numbered step counter advances")

drag(ed, "blur", (20, 20), (120, 80))
out = ed.final().toImage()
cell = {out.pixelColor(x, y).name() for x in range(64, 76) for y in range(64, 76)}
check(len(cell) <= 2, "pixelate flattens a mosaic cell (device pixels, dpr 2)")
check(out.pixelColor(390, 190) == before.pixelColor(390, 190), "pixelate leaves outside untouched")
check(ed.final().devicePixelRatio() == 2.0 and ed.final().width() == 400, "export keeps full HiDPI size")

n = len(ed._undo)
ed.undo(); ed.undo(); ed.redo()
check(len(ed._undo) == n - 1 and len(ed._redo) == 1, "undo / redo bookkeeping")
ed.set_stroke(5)
check(ed.stroke == 2, "stroke index wraps")

# ── editor lifetime & memory ─────────────────────────────────────────────────
ed.flash("Copied")                       # timer pending when the window closes
ed.close()
check(ed not in editor.AnnotationWindow._open and not ed._undo, "close drops registry + undo memory")
t = time.time()
while time.time() - t < 1.8:             # let the 1.6 s flash timer fire after close
    app.processEvents()
check(True, "flash timer after close doesn't crash")
check(not isValid(ed), "closed editor's Qt object is deleted")

big = editor.AnnotationWindow(QPixmap(3840, 2160), QRect(0, 0, 3840, 2160))
check(big.undo_depth * 3840 * 2160 * 4 <= editor.UNDO_BUDGET, "undo depth capped by memory budget")
check(big.scale < 1 and big.view_size.width() <= 1920, "oversized capture scales to fit its monitor")
big.close()

# ── multi-monitor placement ──────────────────────────────────────────────────
editor.AnnotationWindow.last_pos = None
right = editor.AnnotationWindow(noise(300, 150), QRect(2500, 400, 300, 150))
check(right.x() >= 1920, f"editor opens on the selection's monitor (x={right.x()})")
right.close()
editor.AnnotationWindow.last_pos = QPoint(9000, 9000)      # monitor since unplugged
gone = editor.AnnotationWindow(noise(300, 150), QRect(100, 100, 300, 150))
check(gone.x() < 3840, "stale off-screen position is ignored")
gone.close()
editor.AnnotationWindow.last_pos = None

# ── overlay ──────────────────────────────────────────────────────────────────
desk = QPixmap(600, 400)
desk.fill(QColor("#888"))
ov = OverlayWindow(CaptureResult(desk, QRect(100, 50, 600, 400), 1.0))
got = []
ov.region_selected.connect(lambda pm, r: got.append((pm.size(), r)))
ov.is_drawing, ov.origin, ov.selection = True, QPoint(10, 20), QRect(10, 20, 80, 60)


class _Ev:                                # minimal stand-in for a mouse release
    def button(self):
        from PySide6.QtCore import Qt
        return Qt.MouseButton.LeftButton


ov.mouseReleaseEvent(_Ev())
check(got and got[0][1].topLeft() == QPoint(110, 70), "region is reported in desktop coords")
check(ov.result is None, "overlay frees its desktop pixmaps on close")

# ── tray: re-entrancy + IPC ──────────────────────────────────────────────────
import kapture.tray as tray_mod  # noqa: E402
calls = []
tray = tray_mod.TrayApp.__new__(tray_mod.TrayApp)
QSystemTrayIcon.__init__(tray)
tray.app, tray._capturing, tray._ipc = app, False, None
tray.overlay = OverlayWindow(CaptureResult(desk, QRect(0, 0, 600, 400), 1.0))
tray._capture = lambda full: calls.append(full)
tray.start_capture(delay_ms=0)
app.processEvents()
check(not calls, "no second capture while the overlay is open")
tray.overlay.close()
tray.capture_now = lambda: calls.append("ipc")
tray_mod.IPC_NAME = config.IPC_NAME = f"kapture-selftest-{os.getpid()}"
tray.start_ipc_server()
sock = QLocalSocket()
sock.connectToServer(tray_mod.IPC_NAME)
sock.waitForConnected(500)
sock.write(b"capture")                   # written before the server reads it
sock.flush()
t = time.time()
while "ipc" not in calls and time.time() - t < 2:
    app.processEvents()
check("ipc" in calls, "IPC capture request is never lost")

# ── smoothness budget (paint per drag frame) ─────────────────────────────────
ed = editor.AnnotationWindow(noise(1200, 700), QRect(0, 0, 1200, 700))
ed.select_tool("pen")
ed.press(QPoint(10, 10))
for i in range(300):
    ed.drag_to(QPoint(10 + (i * 37) % 1180, 10 + (i * 23) % 680))
app.processEvents()
t = time.perf_counter()
for i in range(60):
    ed.drag_to(QPoint(20 + (i * 41) % 1100, 20 + (i * 29) % 600))
    app.processEvents()
ms = (time.perf_counter() - t) / 60 * 1000
check(ms < 4, f"pen drag frame stays cheap on a long stroke ({ms:.2f} ms)")
ed.release(QPoint(30, 30))
ed.close()

print("all checks passed")
