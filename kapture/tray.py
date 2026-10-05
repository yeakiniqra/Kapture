"""System-tray controller: menu, capture flow, hotkeys, single-instance IPC."""

import asyncio
import logging
import os
import time

from pynput import keyboard
from PySide6.QtCore import QObject, QRect, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QPixmap
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from kapture import APP_NAME, gnome, icons, spawn
from kapture.capture import ScreenshotEngine
from kapture.config import CAPTURE_DELAY_MS, HOTKEYS, IPC_NAME, binding_label, save_dir, touch_once
from kapture.theme import menu_css, theme
from kapture.ui.dialogs import AboutDialog, HelperSetupDialog, SettingsDialog, show_capture_error
from kapture.ui.editor import AnnotationWindow
from kapture.ui.overlay import OverlayWindow

log = logging.getLogger("kapture")


class _Bridge(QObject):
    """Lives on the GUI thread; emitting from pynput's thread queues the slot."""
    capture = Signal()


class TrayApp(QSystemTrayIcon):
    def __init__(self, app: QApplication):
        super().__init__(self._make_icon(), app)
        self.app = app
        self.engine = ScreenshotEngine()
        self.overlay = None
        self._capturing = False
        self._ipc = None
        self.bridge = _Bridge()
        self.bridge.capture.connect(self.capture_now)

        self._setup_menu()
        self.setToolTip(f"{APP_NAME}\n{binding_label()} to capture")
        self.activated.connect(self._on_activated)
        self.show()

        # X11 fallback hotkey; GNOME (incl. Wayland) uses the custom keybinding,
        # which triggers this instance over IPC.
        self._start_hotkey_listener()
        spawn(self._startup())

    async def _startup(self):
        """GNOME setup is a dozen gsettings subprocesses (~100 ms): run it on a
        worker thread so the tray is responsive immediately."""
        if await asyncio.to_thread(gnome.helper_needs_login) and touch_once("helper_notice"):
            QTimer.singleShot(1500, self._offer_helper_activation)
        if await asyncio.to_thread(gnome.apply_binding) and touch_once("shortcuts_set"):
            self.showMessage(APP_NAME, f"{binding_label()} now captures with Kapture. "
                             "Change it anytime in tray → Settings.",
                             QSystemTrayIcon.MessageIcon.Information, 6000)
        else:
            self.showMessage(APP_NAME, f"Running in background.\nPress {binding_label()} "
                             "to capture a region.", QSystemTrayIcon.MessageIcon.Information, 3000)

    @staticmethod
    def _make_icon() -> QIcon:
        """White symbolic glyph drawn on GNOME's 16 px grid so it matches the weight
        of the panel's own status icons (the colour logo is for launcher / About).
        16/32/48/64 are pixel-exact multiples of that grid; 22/24 serve other panels."""
        ic = QIcon()
        for s in (16, 22, 24, 32, 48, 64):
            ic.addPixmap(icons.pixmap("kapture-symbolic", "#FFFFFF", s))
        return ic

    def _setup_menu(self):
        menu = QMenu()
        # Frameless + translucent so the rounded corners render cleanly.
        menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._menu_icons = []

        def add(icon_name, text, slot):
            act = QAction(text, menu)
            act.triggered.connect(slot)
            menu.addAction(act)
            self._menu_icons.append((act, icon_name))

        add("scan", f"Capture Region  ({binding_label()})", lambda: self.start_capture())
        add("monitor", "Capture Full Screen", lambda: self.start_capture(full=True))
        add("timer", "Capture Region in 3 s", lambda: self.start_capture(delay_ms=3000))
        menu.addSeparator()
        add("folder-open", "Open Screenshots Folder",
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(save_dir())))
        menu.addSeparator()
        add("settings", "Settings", self._show_settings)
        add("keyboard", "Set Capture Shortcut", self._setup_shortcut_interactive)
        menu.addSeparator()
        add("info", f"About {APP_NAME}", lambda: AboutDialog().exec())
        add("power", "Quit", self.app.quit)

        menu.aboutToShow.connect(lambda: self._retheme(menu))
        self._retheme(menu)
        self.setContextMenu(menu)
        self._menu = menu

    def _retheme(self, menu: QMenu):
        """Re-theme on every open so a light/dark switch applies without restart."""
        t = theme()
        menu.setStyleSheet(menu_css(t))
        for act, name in self._menu_icons:
            act.setIcon(icons.icon(name, t, 16, menu=True))

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.capture_now()

    def capture_now(self):
        """Hotkey / IPC / double-click: no tray menu to wait out, so no delay."""
        self.start_capture(delay_ms=0)

    # ── capture flow ─────────────────────────────────────────────────────────

    def start_capture(self, full: bool = False, delay_ms: int = CAPTURE_DELAY_MS):
        # One capture at a time: a second hotkey press while the overlay is up
        # (or during the pre-capture delay) would grab the dimmed overlay itself.
        if self._capturing or (self.overlay is not None and self.overlay.isVisible()):
            return
        self._capturing = True
        # Short delay so the tray menu / notification are gone before the grab.
        QTimer.singleShot(delay_ms, lambda: spawn(self._capture(full)))

    async def _capture(self, full: bool):
        try:
            result = await self.engine.capture_full_screen()
        finally:
            self._capturing = False
        if result is None or result.pixmap.isNull():
            log.error("tray: capture failed")
            show_capture_error()
            return
        if full:
            await self._save_full(result.pixmap)
            return
        self.overlay = OverlayWindow(result)
        self.overlay.region_selected.connect(self._open_editor)
        self.overlay.fullscreen_selected.connect(lambda pm: spawn(self._save_full(pm)))

    def _open_editor(self, cropped: QPixmap, region: QRect):
        AnnotationWindow(cropped, region)       # keeps itself alive while open

    async def _save_full(self, pixmap: QPixmap):
        """Full-screen shots skip the editor: copied + saved straight to the folder."""
        QApplication.clipboard().setPixmap(pixmap)
        path = os.path.join(save_dir(), f"kapture_{time.strftime('%Y%m%d_%H%M%S')}.png")
        ok = await asyncio.to_thread(pixmap.toImage().save, path)
        self.showMessage(APP_NAME, f"Saved {os.path.basename(path)} · copied to clipboard"
                         if ok else f"Couldn't save to {path}",
                         QSystemTrayIcon.MessageIcon.Information, 4000)

    # ── hotkeys & IPC ────────────────────────────────────────────────────────

    def _start_hotkey_listener(self):
        hotkeys = [keyboard.HotKey(keyboard.HotKey.parse(hk), self.bridge.capture.emit)
                   for hk in HOTKEYS]

        def on_press(k):
            for hk in hotkeys:
                hk.press(listener.canonical(k))

        def on_release(k):
            for hk in hotkeys:
                hk.release(listener.canonical(k))

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.daemon = True
        listener.start()

    def start_ipc_server(self):
        """Per-user local socket so `kapture --capture` (the GNOME shortcut)
        triggers THIS instance instead of spawning a duplicate tray app."""
        QLocalServer.removeServer(IPC_NAME)        # clear any stale socket
        self._ipc = QLocalServer(self)
        if not self._ipc.listen(IPC_NAME):
            log.warning("tray: IPC listen failed: %s", self._ipc.errorString())
            return
        self._ipc.newConnection.connect(self._on_ipc)

    def _on_ipc(self):
        conn = self._ipc.nextPendingConnection()
        if conn is None:
            return

        def read():
            data = conn.readAll().data().decode("utf-8", "ignore")
            if not data:
                return
            log.info("tray: IPC %r", data)
            if "capture" in data:
                self.capture_now()
            conn.disconnectFromServer()

        conn.disconnected.connect(conn.deleteLater)     # one socket per request: free it
        conn.readyRead.connect(read)
        if conn.bytesAvailable():                       # sent before we were listening
            read()

    # ── dialogs ──────────────────────────────────────────────────────────────

    def _offer_helper_activation(self):
        dlg = HelperSetupDialog()
        dlg.exec()
        if dlg.logout:
            gnome.logout()

    def _show_settings(self):
        SettingsDialog(apply_cb=self._apply_binding).exec()

    def _apply_binding(self, binding: str):
        spawn(asyncio.to_thread(gnome.apply_binding, binding))
        self._menu.actions()[0].setText(f"Capture Region  ({binding_label()})")
        self.setToolTip(f"{APP_NAME}\n{binding_label()} to capture")

    def _setup_shortcut_interactive(self):
        spawn(self._setup_shortcut())

    async def _setup_shortcut(self):
        ok = await asyncio.to_thread(gnome.apply_binding)
        self.showMessage(
            APP_NAME,
            f"{binding_label()} now opens Kapture." if ok else
            "Couldn't set it automatically — add a shortcut in Settings → "
            "Keyboard pointing to:  kapture --capture",
            QSystemTrayIcon.MessageIcon.Information, 6000)
