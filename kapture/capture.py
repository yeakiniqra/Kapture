"""Native full-desktop capture — no external screenshot binaries.

    X11 / XWayland-backed grab  ->  QScreen.grabWindow        (instant, no flash)
    GNOME Wayland               ->  Kapture Shell extension    (flash-free)
    GNOME Wayland (older)       ->  org.gnome.Shell.Screenshot (flash-free where allowed)
    GNOME Wayland (fallback)    ->  XDG desktop portal         (flashes + prompts)
    KDE / other Wayland         ->  XDG desktop portal
    wlroots (Sway/Hyprland)     ->  grim
    nothing worked              ->  None  (caller shows a clear error dialog)
"""

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from urllib.parse import unquote, urlparse

from jeepney import MatchRule, message_bus, new_method_call
from jeepney.io.blocking import Proxy, open_dbus_connection
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import QWidget

from kapture import dbus

log = logging.getLogger("kapture")

# XDG desktop portal (Wayland capture path)
PORTAL = dbus.addr("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                   "org.freedesktop.portal.Screenshot")
_REQUEST_IFACE = "org.freedesktop.portal.Request"

# Kapture's bundled GNOME Shell extension exposes a flash-free, prompt-free
# capture over D-Bus from INSIDE the Shell's privileged context — the only way
# to avoid GNOME's shutter flash on Wayland (Mutter refuses Shell.Screenshot for
# external apps, the portal always flashes, and grim needs wlr-screencopy).
# Installed by the .deb to /usr/share/gnome-shell/extensions; enabled per user.
EXT_UUID    = "kapture-screenshot@yeakiniqra.github.io"
EXT_SERVICE = "org.kapture.ScreenshotHelper"
EXT = dbus.addr(EXT_SERVICE, "/org/kapture/ScreenshotHelper", "org.kapture.ScreenshotHelper")

# GNOME's own screenshot interface.  On GNOME 41+ Mutter refuses this for
# unconfined callers ("Screenshot is not allowed"); kept only as a best-effort
# path for older GNOME where it still works flash-free (flash=False).
GNOME_SHELL = dbus.addr("org.gnome.Shell.Screenshot", "/org/gnome/Shell/Screenshot",
                        "org.gnome.Shell.Screenshot")

# On Wayland (GNOME/KDE) the portal shows a one-time "Allow … to take
# screenshots?" consent prompt on first use; the Response signal only arrives
# after the user answers it.  The timeout therefore has to allow human reaction
# time — it is a safety net for a genuinely stuck portal, not a UX deadline.
PORTAL_TIMEOUT_S = 120.0


def session_is_wayland() -> bool:
    """
    Detect the *real* session — NOT QGuiApplication.platformName().

    On GNOME Kapture runs Qt on XWayland ('xcb'), where grabWindow returns a
    BLACK frame, so trusting platformName would silently produce black
    screenshots.  We key off the session environment instead.
    """
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return True
    return bool(os.environ.get("WAYLAND_DISPLAY"))


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
        is re-stamped on the result.  (QPainter.drawPixmap's source rect is in
        device pixels too — the overlay scales it the same way.)
        """
        d = self.dpr
        cropped = self.pixmap.copy(QRect(round(sel.x() * d), round(sel.y() * d),
                                         round(sel.width() * d), round(sel.height() * d)))
        cropped.setDevicePixelRatio(d)
        return cropped


def _wrap(pixmap: QPixmap) -> CaptureResult:
    """Stamp devicePixelRatio so a file-sourced pixmap (helper/portal/grim) aligns
    1:1 with logical virtual geometry, and wrap it in a CaptureResult."""
    screen = QGuiApplication.primaryScreen()
    geo = screen.virtualGeometry()
    dpr = round(pixmap.width() / geo.width(), 4) if geo.width() else screen.devicePixelRatio()
    pixmap.setDevicePixelRatio(dpr or 1.0)
    log.info("capture: %dx%d px (logical %dx%d, dpr=%.2f)",
             pixmap.width(), pixmap.height(), geo.width(), geo.height(), dpr)
    return CaptureResult(pixmap, geo, dpr or 1.0)


def _via_tempfile(grab) -> 'QImage | None':
    """Run grab(tmp_path) -> bool, load the PNG it wrote, always clean up.
    Returns a QImage (not QPixmap) so it can run on a worker thread."""
    fd, tmp = tempfile.mkstemp(suffix=".png", prefix="kapture_")
    os.close(fd)
    try:
        if not grab(tmp):
            return None
        image = QImage(tmp)
        return None if image.isNull() else image
    except Exception as e:
        log.debug("capture: %s", e)
        return None
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


class ScreenshotEngine:
    _token_counter = 0

    def __init__(self):
        self._busy = False
        self._anchor: 'QWidget | None' = None   # realized X11 window for portal parenting

    @property
    def busy(self) -> bool:
        return self._busy

    async def capture_full_screen(self) -> 'CaptureResult | None':
        if self._busy:
            log.warning("capture_full_screen: a capture is already in flight, ignoring")
            return None
        self._busy = True
        try:
            if not session_is_wayland():
                return self._x11_grab()
            # Wayland capture order, best UX first. Never a silent black grabWindow.
            # D-Bus round-trip + PNG decode (~250 ms at 4K) run on a worker
            # thread so the UI never stalls; only the QPixmap is made here.
            for backend in (self._extension, self._gnome_shell):
                image = await asyncio.to_thread(backend)
                if image is not None:
                    return _wrap(QPixmap.fromImage(image))
            if dbus.has_owner(PORTAL.bus_name):
                try:
                    return await self._portal()
                except Exception as e:
                    log.error("capture_full_screen: portal failed: %s", e)
            image = await asyncio.to_thread(self._grim)
            if image is None:
                log.error("capture_full_screen: no working Wayland backend")
                return None
            return _wrap(QPixmap.fromImage(image))
        finally:
            self._busy = False

    # ── backends ─────────────────────────────────────────────────────────────

    @staticmethod
    def _x11_grab() -> 'CaptureResult | None':
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return None
        geo = screen.virtualGeometry()              # logical coords
        dpr = screen.devicePixelRatio()
        pixmap = screen.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())
        if pixmap.isNull():
            log.error("_x11_grab: null pixmap")
            return None
        pixmap.setDevicePixelRatio(dpr)
        return CaptureResult(pixmap, geo, dpr)

    @staticmethod
    def _extension() -> 'QImage | None':
        """Flash-free capture via Kapture's GNOME Shell extension. None if the
        helper isn't loaded yet (no relogin since install) so the portal takes over."""
        if not dbus.has_owner(EXT_SERVICE):
            return None
        return _via_tempfile(lambda tmp: dbus.call(EXT, "CaptureToFile", "s", (tmp,))[0])

    @staticmethod
    def _gnome_shell() -> 'QImage | None':
        """org.gnome.Shell.Screenshot(include_cursor, flash, filename) with
        flash=False. Refused on GNOME 41+ for unconfined apps → None."""
        if not dbus.has_owner(GNOME_SHELL.bus_name):
            return None
        return _via_tempfile(
            lambda tmp: dbus.call(GNOME_SHELL, "Screenshot", "bbs", (False, False, tmp))[0])

    @staticmethod
    def _grim() -> 'QImage | None':
        if shutil.which("grim") is None:
            return None
        return _via_tempfile(lambda tmp: subprocess.run(
            ["grim", tmp], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ).returncode == 0 and os.path.getsize(tmp) > 0)

    def _parent_window_token(self) -> str:
        """
        'x11:<xid>' of a realized off-screen window. GNOME's portal associates the
        Request with it; an empty token has been seen to fail with response=2
        ("Failed to associate portal window with parent window").
        """
        if QGuiApplication.platformName() != "xcb":
            return ""
        try:
            if self._anchor is None:
                w = QWidget()
                w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
                w.resize(1, 1)
                w.show()
                self._anchor = w
            xid = int(self._anchor.winId())
            return "x11:%x" % xid if xid else ""
        except Exception as e:
            log.debug("_parent_window_token: %s", e)
            return ""

    async def _portal(self) -> CaptureResult:
        ScreenshotEngine._token_counter += 1
        token = f"kapture_{os.getpid()}_{ScreenshotEngine._token_counter}"
        parent = self._parent_window_token()      # Qt call — stay on the GUI thread
        # The wait for the user's consent can take a while: block a worker thread,
        # never the Qt/asyncio loop.
        response, results = await asyncio.to_thread(_portal_request, parent, token)
        if response == 1:
            raise RuntimeError("screenshot permission denied / cancelled by user")
        if response != 0:
            raise RuntimeError(f"portal returned error (response={response})")
        uri = results.get("uri", ("s", ""))[1]
        if not uri:
            raise RuntimeError("portal Response had no uri")
        path = unquote(urlparse(uri).path)
        pixmap = QPixmap(path)
        try:
            os.unlink(path)             # portal stores a throwaway file; clean it up
        except OSError:
            pass
        if pixmap.isNull():
            raise RuntimeError(f"failed to load portal image {path}")
        return _wrap(pixmap)


def _portal_request(parent: str, token: str) -> tuple:
    """Screenshot(parent, a{sv}) → wait for Request.Response(u, a{sv})."""
    with open_dbus_connection(bus="SESSION") as conn:
        sender = conn.unique_name.lstrip(":").replace(".", "_")
        # The Request path is deterministic (xdg-desktop-portal spec): subscribe
        # BEFORE the call so an instant answer can't be missed.
        rule = MatchRule(type="signal", interface=_REQUEST_IFACE, member="Response",
                         path=f"/org/freedesktop/portal/desktop/request/{sender}/{token}")
        Proxy(message_bus, conn).AddMatch(rule)
        with conn.filter(rule) as queue:
            options = {"handle_token": ("s", token),
                       "interactive": ("b", False),    # whole screen, no picker UI
                       "modal": ("b", False)}
            conn.send_and_get_reply(
                new_method_call(PORTAL, "Screenshot", "sa{sv}", (parent, options)), timeout=10)
            return conn.recv_until_filtered(queue, timeout=PORTAL_TIMEOUT_S).body
