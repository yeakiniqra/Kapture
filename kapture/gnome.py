"""GNOME integration: the capture keybinding, Print-key handover, Shell helper."""

import ast
import json
import logging
import os
import shutil
import subprocess
import sys

from kapture import dbus
from kapture.capture import EXT_UUID, EXT_SERVICE, session_is_wayland
from kapture.config import CONFIG, GNOME_SS_BACKUP, resource_path
from kapture.theme import is_gnome

log = logging.getLogger("kapture")

_MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
_SHELL_KEYS = "org.gnome.shell.keybindings"
# GNOME's own screenshot accelerators that can collide with `Print`.
_SS_KEYS = ("show-screenshot-ui", "screenshot", "screenshot-window")
_SS_DEFAULTS = {"show-screenshot-ui": ["Print"],
                "screenshot": ["<Shift>Print"],
                "screenshot-window": ["<Alt>Print"]}


def available() -> bool:
    return is_gnome() and bool(shutil.which("gsettings"))


def _get(*args) -> str:
    return subprocess.run(["gsettings", "get", *args],
                          capture_output=True, text=True, timeout=5).stdout.strip()


def _set(*args):
    subprocess.run(["gsettings", "set", *args], timeout=5)


def _parse_list(raw: str) -> list:
    """Parse a gsettings 'as' value ('@as []', "['Print']") into a list."""
    raw = (raw or "").strip()
    if raw.startswith("@as"):
        raw = raw[raw.find("["):]
    try:
        return list(ast.literal_eval(raw)) if raw.startswith("[") else []
    except (ValueError, SyntaxError):
        return []


def _fmt_list(vals) -> str:
    return "[" + ", ".join("'%s'" % v for v in vals) + "]"


def _gv_str(s: str) -> str:
    """Serialize a string as a GVariant literal `gsettings set` parses verbatim.

    `gsettings set ... command "/usr/bin/kapture" --capture` FAILS: a value that
    begins with a quote is read as a GVariant string and the trailing text trips
    the parser, so the command silently never gets stored. Wrapping the whole
    value in escaped double quotes makes it an unambiguous literal.
    """
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def launch_command() -> str:
    """Shell command GNOME runs for the shortcut (paths quoted for spaces)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --capture'
    return f'"{sys.executable}" "{resource_path("main.py")}" --capture'


_KAPTURE_KEYBINDING = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/kapture/"
_HELPER_SHORTCUT = "/org/gnome/shell/extensions/kapture-screenshot/capture-shortcut"


def _register_keybinding(binding: str):
    path = _KAPTURE_KEYBINDING
    paths = _parse_list(_get(_MEDIA_KEYS, "custom-keybindings"))
    if path not in paths:
        _set(_MEDIA_KEYS, "custom-keybindings", _fmt_list(paths + [path]))
    schema = f"{_MEDIA_KEYS}.custom-keybinding:{path}"
    _set(schema, "name", _gv_str("Kapture — Capture"))
    _set(schema, "command", _gv_str(launch_command()))
    _set(schema, "binding", _gv_str(binding))


def _backup_screenshot_keys():
    """Save GNOME's screenshot keybindings once, before we touch them. Never
    overwrites an existing backup (that would capture our own emptied values)."""
    if os.path.exists(GNOME_SS_BACKUP):
        return
    snapshot = {key: _parse_list(_get(_SHELL_KEYS, key)) for key in _SS_KEYS}
    try:
        os.makedirs(os.path.dirname(GNOME_SS_BACKUP), exist_ok=True)
        with open(GNOME_SS_BACKUP, "w") as f:
            json.dump(snapshot, f, indent=2)
    except OSError as e:
        log.debug("_backup_screenshot_keys: %s", e)


def _free_print():
    """Remove 'Print' from GNOME's screenshot keybindings so ours takes effect."""
    _backup_screenshot_keys()
    for key in _SS_KEYS:
        vals = _parse_list(_get(_SHELL_KEYS, key))
        if "Print" in vals:
            _set(_SHELL_KEYS, key, _fmt_list(v for v in vals if v != "Print"))
            log.info("_free_print: removed Print from %s", key)


def restore_screenshot_keys():
    """Give GNOME its screenshot keybindings back (backup, else factory defaults).
    No backup => Kapture never took Print, so there is nothing to restore."""
    if not os.path.exists(GNOME_SS_BACKUP):
        return
    try:
        with open(GNOME_SS_BACKUP) as f:
            snapshot = json.load(f)
    except (OSError, ValueError):
        snapshot = None
    restore = snapshot if isinstance(snapshot, dict) and snapshot else _SS_DEFAULTS
    for key, vals in restore.items():
        _set(_SHELL_KEYS, key, _fmt_list(vals))
    try:
        os.remove(GNOME_SS_BACKUP)
    except OSError:
        pass


def _unregister_keybinding():
    paths = _parse_list(_get(_MEDIA_KEYS, "custom-keybindings"))
    if _KAPTURE_KEYBINDING in paths:
        _set(_MEDIA_KEYS, "custom-keybindings",
             _fmt_list(p for p in paths if p != _KAPTURE_KEYBINDING))


def apply_binding(binding: str = None) -> bool:
    """Bind the capture shortcut (idempotent). With the Shell helper active, the
    helper owns the key (flash-free captures) and our custom keybinding is
    removed so the two never grab the same accelerator; otherwise a GNOME custom
    keybinding runs `kapture --capture`. Only displaces GNOME's native
    screenshot when the user explicitly picked Print."""
    if not available():
        return False
    binding = binding or CONFIG.get("capture_binding", "<Control><Shift>s")
    try:
        if dbus.has_owner(EXT_SERVICE):
            # dconf writes the helper's key directly — no schema lookup needed,
            # wherever the extension is installed. The Shell picks it up live.
            subprocess.run(["dconf", "write", _HELPER_SHORTCUT, _fmt_list([binding])],
                           check=True, timeout=5)
            _unregister_keybinding()
        else:
            _register_keybinding(binding)
        if binding == "Print":
            _free_print()
        else:
            restore_screenshot_keys()
        log.info("apply_binding: %s now launches Kapture", binding)
        return True
    except Exception as e:
        log.warning("apply_binding: %s", e)
        return False


# ── flash-free Shell helper ──────────────────────────────────────────────────

def helper_needs_login() -> bool:
    """On GNOME Wayland: True if the helper extension is installed but not yet
    running. Pre-registers it so GNOME enables it at the next login."""
    if not (session_is_wayland() and is_gnome()) or dbus.has_owner(EXT_SERVICE):
        return False
    installed = any(
        os.path.isfile(os.path.join(d, EXT_UUID, "metadata.json"))
        for d in ("/usr/share/gnome-shell/extensions",
                  os.path.expanduser("~/.local/share/gnome-shell/extensions")))
    if not installed:
        return False
    # `gnome-extensions enable` fails until the Shell has scanned a freshly
    # installed extension, so edit enabled-extensions directly (CLI as a bonus).
    try:
        current = _parse_list(_get("org.gnome.shell", "enabled-extensions"))
        if EXT_UUID not in current:
            _set("org.gnome.shell", "enabled-extensions", _fmt_list(current + [EXT_UUID]))
        if shutil.which("gnome-extensions"):
            subprocess.run(["gnome-extensions", "enable", EXT_UUID],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    except Exception as e:
        log.debug("helper_needs_login: %s", e)
    return True


def logout():
    """Politely request a logout (GNOME shows its own confirmation)."""
    if shutil.which("gnome-session-quit"):
        subprocess.Popen(["gnome-session-quit", "--logout"])
        return
    try:  # mode 0 = normal logout (shows confirmation)
        dbus.call(dbus.addr("org.gnome.SessionManager", "/org/gnome/SessionManager",
                            "org.gnome.SessionManager"), "Logout", "u", (0,))
    except Exception as e:
        log.debug("logout: %s", e)
