"""Windows integration: Print Screen handover and start-with-Windows.

Mirrors gnome.py — Kapture only displaces the OS's own Print Screen handler
(Snipping Tool) when the user explicitly picks "Print Screen", and gives it
back the moment they switch away. Everything here is a no-op off Windows.
"""

import logging
import sys

from kapture.config import CONFIG, WINDOWS, save_config

log = logging.getLogger("kapture")

_KEYBOARD = r"Control Panel\Keyboard"
_SNIP_VALUE = "PrintScreenKeyForSnippingEnabled"     # Win 11: Print opens Snipping Tool
_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"


def available() -> bool:
    return WINDOWS


def _get(key_path: str, name: str):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def _set(key_path: str, name: str, value, kind: int):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as k:
        winreg.SetValueEx(k, name, 0, kind, value)


def _delete(key_path: str, name: str):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
    except OSError:
        pass


def apply_binding(binding: str = None):
    """Hand Print Screen over to Kapture (or back to Windows) to match `binding`."""
    if not available():
        return
    import winreg
    binding = binding or CONFIG.get("capture_binding", "<Control><Shift>s")
    try:
        if binding == "Print":
            if CONFIG.get("win_snip_backup") is None:           # back up once, before we touch it
                current = _get(_KEYBOARD, _SNIP_VALUE)
                CONFIG["win_snip_backup"] = 1 if current is None else int(current)
                save_config(CONFIG)
            _set(_KEYBOARD, _SNIP_VALUE, 0, winreg.REG_DWORD)
            log.info("windows.apply_binding: Print Screen now opens Kapture (Snipping Tool off)")
        elif CONFIG.get("win_snip_backup") is not None:
            _set(_KEYBOARD, _SNIP_VALUE, int(CONFIG["win_snip_backup"]), winreg.REG_DWORD)
            CONFIG["win_snip_backup"] = None
            save_config(CONFIG)
            log.info("windows.apply_binding: Print Screen handed back to Windows")
    except OSError as e:
        log.warning("windows.apply_binding: %s", e)


def bring_to_front(widget):
    """Give `widget` keyboard focus even though Kapture is a background tray app.

    Windows' foreground lock refuses SetForegroundWindow from a process that
    didn't receive the last input — and a global-hotkey capture never does —
    so the overlay would show without focus and Esc / Enter would go nowhere.
    Briefly attaching to the foreground window's input queue lifts the lock.
    """
    if not available():
        return
    try:
        import ctypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        hwnd = int(widget.winId())
        fg_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        me = kernel32.GetCurrentThreadId()
        attached = fg_thread != me and user32.AttachThreadInput(fg_thread, me, True)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        if attached:
            user32.AttachThreadInput(fg_thread, me, False)
    except Exception as e:                  # never let focus trouble break a capture
        log.debug("windows.bring_to_front: %s", e)


def autostart_enabled() -> bool:
    return available() and _get(_RUN, "Kapture") is not None


def set_autostart(enabled: bool):
    """Register / unregister this executable in HKCU\\...\\Run."""
    if not available():
        return
    import winreg
    try:
        if enabled:
            exe = sys.executable if getattr(sys, "frozen", False) else None
            if exe is None:                                     # running from source
                log.info("windows.set_autostart: skipped (not a packaged build)")
                return
            _set(_RUN, "Kapture", f'"{exe}"', winreg.REG_SZ)
        else:
            _delete(_RUN, "Kapture")
    except OSError as e:
        log.warning("windows.set_autostart: %s", e)
