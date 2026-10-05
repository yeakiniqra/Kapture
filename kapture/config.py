"""User settings (~/.config/kapture/config.json), shared constants and paths."""

import json
import logging
import os
import sys

log = logging.getLogger("kapture")

# X11-only fallback hotkeys (pynput). On Wayland these are inert — global key
# grabbing is impossible for an unprivileged app, so the GNOME custom keybinding
# (org.gnome.settings-daemon ... custom-keybindings) does the work there instead.
# Print is deliberately NOT here: Kapture no longer fights GNOME for the Print key;
# the native GNOME screenshot keeps it unless the user opts in via Settings.
HOTKEYS = ["<ctrl>+<shift>+s"]

# Per-user single-instance / remote-trigger socket name.
IPC_NAME = f"kapture-{os.getuid()}" if hasattr(os, "getuid") else "kapture"

# Delay between trigger and capture, just long enough for the tray menu /
# startup notification to disappear so they don't land in the screenshot.
CAPTURE_DELAY_MS = 150

CONFIG_PATH = os.path.expanduser("~/.config/kapture/config.json")
# Where we stash GNOME's original screenshot keybindings before we steal `Print`,
# so we can hand them back when Kapture's hotkey changes or the app is removed.
GNOME_SS_BACKUP = os.path.expanduser("~/.config/kapture/gnome_screenshot_backup.json")
CACHE_DIR = os.path.expanduser("~/.cache/kapture")

_DEFAULTS = {
    "save_dir": os.path.expanduser("~/Pictures"),
    "auto_save": False,                 # Save without showing the file dialog
    # GNOME accelerator for the capture shortcut. Default is Ctrl+Shift+S so we
    # never displace GNOME's native Print screenshot; the user can switch to Print
    # (or any key) in Settings, which is the explicit opt-in to take it over.
    "capture_binding": "<Control><Shift>s",
    "color": "#E63232",                 # last annotation colour
    "stroke": 1,                        # index into STROKES
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


# Pretty names for GNOME accelerators, shared by the tray UI and Settings dialog.
SHORTCUTS = [
    ("Print Screen",        "Print"),
    ("Ctrl + Shift + S",    "<Control><Shift>s"),
    ("Ctrl + Print Screen", "<Control>Print"),
    ("Super + Shift + S",   "<Super><Shift>s"),
]


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
