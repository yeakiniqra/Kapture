"""User settings, shared constants and per-platform paths.

Linux:   ~/.config/kapture/config.json, ~/.cache/kapture
Windows: %APPDATA%\\Kapture\\config.json, %LOCALAPPDATA%\\Kapture\\cache
"""

import json
import logging
import os
import sys

from PySide6.QtCore import QStandardPaths

log = logging.getLogger("kapture")

WINDOWS = sys.platform == "win32"

# Per-user single-instance / remote-trigger socket name (a named pipe on Windows).
IPC_NAME = (f"kapture-{os.getuid()}" if hasattr(os, "getuid")
            else f"kapture-{os.environ.get('USERNAME', 'user')}")

# Delay between trigger and capture, just long enough for the tray menu /
# startup notification to disappear so they don't land in the screenshot.
CAPTURE_DELAY_MS = 150

if WINDOWS:
    _CONFIG_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Kapture")
    CACHE_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or _CONFIG_DIR, "Kapture", "cache")
else:
    _CONFIG_DIR = os.path.expanduser("~/.config/kapture")
    CACHE_DIR = os.path.expanduser("~/.cache/kapture")
CONFIG_PATH = os.path.join(_CONFIG_DIR, "config.json")
# Where we stash GNOME's original screenshot keybindings before we steal `Print`,
# so we can hand them back when Kapture's hotkey changes or the app is removed.
GNOME_SS_BACKUP = os.path.join(_CONFIG_DIR, "gnome_screenshot_backup.json")

_DEFAULTS = {
    # The user's real Pictures folder (OneDrive-redirected on Windows, localised on Linux).
    "save_dir": (QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
                 or os.path.expanduser("~/Pictures")),
    "auto_save": False,                 # Save without showing the file dialog
    # Capture shortcut, stored as a GNOME accelerator (translated for pynput on
    # other platforms). Default is Ctrl+Shift+S so we never displace the OS's
    # native Print Screen; picking "Print Screen" in Settings is the explicit
    # opt-in to take it over.
    "capture_binding": "<Control><Shift>s",
    "color": "#E63232",                 # last annotation colour
    "stroke": 1,                        # index into STROKES
    "autostart": None,                  # Windows: start with Windows (None = never asked)
    "win_snip_backup": None,            # Windows: Snipping-Tool-on-Print value before we took Print
}


def load_config() -> dict:
    cfg = dict(_DEFAULTS)
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update({k: data[k] for k in _DEFAULTS if k in data})
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
        log.info("save_config: wrote %s", CONFIG_PATH)
    except OSError as e:
        log.warning("save_config: %s", e)


CONFIG = load_config()


def save_dir() -> str:
    """Configured screenshot folder, created on demand (home as last resort)."""
    d = CONFIG.get("save_dir") or os.path.expanduser("~/Pictures")
    try:
        os.makedirs(d, exist_ok=True)
        return d
    except OSError:
        return os.path.expanduser("~")


# Capture shortcuts offered in Settings: (label, GNOME accelerator). Win+Shift+S
# is reserved by Windows' Snipping Tool, so Windows gets Alt+Shift+S instead.
SHORTCUTS = [
    ("Print Screen",        "Print"),
    ("Ctrl + Shift + S",    "<Control><Shift>s"),
    ("Ctrl + Print Screen", "<Control>Print"),
    ("Alt + Shift + S",     "<Alt><Shift>s") if WINDOWS else ("Super + Shift + S", "<Super><Shift>s"),
]

# The same accelerators in pynput's HotKey syntax (Windows / non-GNOME X11).
PYNPUT_BINDINGS = {
    "Print":             "<print_screen>",
    "<Control><Shift>s": "<ctrl>+<shift>+s",
    "<Control>Print":    "<ctrl>+<print_screen>",
    "<Alt><Shift>s":     "<alt>+<shift>+s",
    "<Super><Shift>s":   "<cmd>+<shift>+s",
}


def binding_label(binding: str = None) -> str:
    binding = binding or CONFIG.get("capture_binding", "<Control><Shift>s")
    return dict((b, label) for label, b in SHORTCUTS).get(binding, binding)


def resource_path(relative: str) -> str:
    """Absolute path to a bundled resource — works from source and PyInstaller."""
    base = getattr(sys, "_MEIPASS",
                   os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, relative)


def touch_once(name: str) -> bool:
    """True the first time it's called for `name` (persisted in the cache dir)."""
    stamp = os.path.join(CACHE_DIR, name)
    if os.path.exists(stamp):
        return False
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        open(stamp, "w").close()
    except OSError:
        pass
    return True
