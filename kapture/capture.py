"""Native full-desktop capture — no external screenshot binaries.

    Windows                     ->  QScreen.grabWindow per monitor, composited
    X11 / XWayland-backed grab  ->  QScreen.grabWindow        (instant, no flash)
    GNOME Wayland, shortcut     ->  Kapture Shell extension takes the shot itself and
                                    opens it in Kapture (flash-free; see tray._open_file)
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

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from kapture import dbus
from kapture.config import WINDOWS

log = logging.getLogger("kapture")

# XDG desktop portal (Wayland capture path)
PORTAL = dbus.addr("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                   "org.freedesktop.portal.Screenshot")
_REQUEST_IFACE = "org.freedesktop.portal.Request"

# Kapture's GNOME Shell extension owns the capture shortcut: on a real keypress
# it captures from INSIDE the Shell (no shutter flash, no portal prompt) and opens
# the image in Kapture. It deliberately has no API — nothing can ask it for a
# screenshot — and owns this bus name only so Kapture can tell it's active.
# Installed by the .deb to /usr/share/gnome-shell/extensions; enabled per user.
EXT_UUID    = "kapture-screenshot@yeakiniqra.github.io"
EXT_SERVICE = "io.github.yeakiniqra.Kapture.ShellHelper"
# Where the extension drops its screenshots (~/.cache/kapture/shots).
SHOTS_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                         "kapture", "shots")

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


def from_file(path: str) -> 'QImage | None':
    """Load a screenshot handed to Kapture as a file (the Shell helper's shots).
    Thread-safe. Files from the helper's private shots dir are consumed (deleted)."""
    image = QImage(path)
    if os.path.dirname(os.path.realpath(path)) == os.path.realpath(SHOTS_DIR):
        try:
            os.unlink(path)
        except OSError:
            pass
    return None if image.isNull() else image


def sweep_shots(max_age_s: float = 60):
    """Delete helper screenshots nobody opened (e.g. Kapture failed to start)."""
    import time
    try:
        for name in os.listdir(SHOTS_DIR):
            path = os.path.join(SHOTS_DIR, name)
            if time.time() - os.path.getmtime(path) > max_age_s:
                os.unlink(path)
    except OSError:
        pass


def wrap_image(image: QImage) -> CaptureResult:
    return _wrap(QPixmap.fromImage(image))


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
            if WINDOWS:
                return self._composite_grab()
            if not session_is_wayland():
                return self._x11_grab()
            # Wayland capture order, best UX first. Never a silent black grabWindow.
            # D-Bus round-trip + PNG decode (~250 ms at 4K) run on a worker
            # thread so the UI never stalls; only the QPixmap is made here.
            image = await asyncio.to_thread(self._gnome_shell)
            if image is not None:
                return _wrap(QPixmap.fromImage(image))
            if dbus.has_owner(PORTAL[1]):
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
    def _composite_grab() -> 'CaptureResult | None':
        """Windows: QScreen.grabWindow(0) only covers that QScreen, so grab each
        monitor and composite them onto the virtual desktop at the primary DPR."""
        primary = QGuiApplication.primaryScreen()
        if primary is None:
            return None
        geo, dpr = primary.virtualGeometry(), primary.devicePixelRatio()
        canvas = QPixmap(round(geo.width() * dpr), round(geo.height() * dpr))
        canvas.fill(Qt.GlobalColor.black)
        p = QPainter(canvas)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for screen in QGuiApplication.screens():
            grab = screen.grabWindow(0)
            if grab.isNull():
                log.warning("_composite_grab: null grab from %s", screen.name())
                continue
            r = screen.geometry().translated(-geo.topLeft())
            # ponytail: one DPR for the whole desktop — a monitor with a different
            # scale is resampled; per-screen overlays would be exact.
            p.drawPixmap(QRectF(r.x() * dpr, r.y() * dpr, r.width() * dpr, r.height() * dpr),
                         grab, QRectF(grab.rect()))
        p.end()
        canvas.setDevicePixelRatio(dpr)
        return CaptureResult(canvas, geo, dpr)

    @staticmethod
    def _gnome_shell() -> 'QImage | None':
        """org.gnome.Shell.Screenshot(include_cursor, flash, filename) with
        flash=False. Refused on GNOME 41+ for unconfined apps → None."""
        if not dbus.has_owner(GNOME_SHELL[1]):
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
    from jeepney import DBusAddress, MatchRule, message_bus, new_method_call
    from jeepney.io.blocking import Proxy, open_dbus_connection
    portal = DBusAddress(PORTAL[0], bus_name=PORTAL[1], interface=PORTAL[2])
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
                new_method_call(portal, "Screenshot", "sa{sv}", (parent, options)), timeout=10)
            return conn.recv_until_filtered(queue, timeout=PORTAL_TIMEOUT_S).body
