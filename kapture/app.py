"""Entry point: environment, single-instance handoff, Qt + asyncio event loop."""

import asyncio
import logging
import os
import sys

# Must precede any Qt import: qasync binds to whichever Qt it's told to.
os.environ.setdefault("QT_API", "pyside6")
# On GNOME Wayland run Qt on XWayland, as Kapture always has: the overlay needs
# to cover the screen at a fixed position and the editor needs move() — both
# impossible for a native Wayland client. (Captures still use the Wayland
# backends; see capture.session_is_wayland.)
if (os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        and "gnome" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()):
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

import qasync  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtNetwork import QLocalSocket  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon  # noqa: E402

from kapture import APP_NAME  # noqa: E402
from kapture.config import CACHE_DIR, IPC_NAME, WINDOWS, resource_path  # noqa: E402
from kapture.theme import load_fonts, sans  # noqa: E402

log = logging.getLogger("kapture")


def _setup_logging():
    handlers = None
    if sys.stderr is None:                      # windowed (console-less) build: log to a file
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            handlers = [logging.FileHandler(os.path.join(CACHE_DIR, "kapture.log"), encoding="utf-8")]
        except OSError:
            handlers = [logging.NullHandler()]
    logging.basicConfig(level=logging.DEBUG if os.environ.get("KAPTURE_DEBUG") else logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", handlers=handlers)


def main():
    _setup_logging()
    if WINDOWS:                                 # own taskbar identity (icon, grouping)
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("yeakiniqra.Kapture")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName("io.github.yeakiniqra.Kapture")
    app.setQuitOnLastWindowClosed(False)            # stay alive in the tray

    want_capture = "--capture" in sys.argv[1:]

    # Single instance: if Kapture is already running, forward the request.
    probe = QLocalSocket()
    probe.connectToServer(IPC_NAME)
    if probe.waitForConnected(300):
        probe.write(b"capture" if want_capture else b"show")
        probe.flush()
        probe.waitForBytesWritten(500)
        probe.disconnectFromServer()
        log.info("main: forwarded to the running instance; exiting")
        return

    load_fonts()
    app.setFont(sans(14))
    app.setWindowIcon(QIcon(resource_path(os.path.join("assets", "app-logo.png"))))

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, APP_NAME, "System tray not available on this desktop.")
        sys.exit(1)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    from kapture.tray import TrayApp            # after QApplication exists
    tray = TrayApp(app)
    tray.start_ipc_server()
    if want_capture:   # launched as `kapture --capture` while not running
        QTimer.singleShot(600, tray.start_capture)

    with loop:
        loop.run_forever()
