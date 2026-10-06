"""Nothing-style theme: monochrome tokens, one accent, flat surfaces, no shadows.

Palette: midnight-violet #27233A · charcoal #505168 · ash-grey #B3C0A4 ·
         beige #EAEFD3 · soft-fawn #DCC48E (the single accent — Save, selection).
Type:    DM Sans (UI) · DM Mono (ALL-CAPS labels and data).
"""

import os
import subprocess
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import QApplication

from kapture.config import resource_path

DARK = dict(
    bg="#27233A", surface="#333148", border="#505168",
    text="#EAEFD3", text_sec="#B3C0A4",
    accent="#DCC48E", on_accent="#27233A", red="#D71921",
)
LIGHT = dict(
    bg="#EAEFD3", surface="#DCE3C7", border="#B3C0A4",
    text="#27233A", text_sec="#505168",
    accent="#DCC48E", on_accent="#27233A", red="#D71921",
)

SANS = "DM Sans"
MONO = "DM Mono"
BTN_H = 40                      # every toolbar / dialog button is this tall


def is_gnome() -> bool:
    return "gnome" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()


_dark_cache = (-1e9, False)        # (monotonic time, result)


def is_dark() -> bool:
    """GNOME: color-scheme ('prefer-dark', GNOME 42+) or a '-dark' gtk-theme
    (older GNOME / Yaru-dark). Elsewhere: the Qt palette's lightness.
    Cached for a few seconds — each gsettings call is a ~6 ms subprocess, and
    this runs on every window / menu open."""
    global _dark_cache
    if time.monotonic() - _dark_cache[0] < 3:
        return _dark_cache[1]
    dark = None
    if is_gnome():
        try:
            for key in ("color-scheme", "gtk-theme"):
                out = subprocess.run(
                    ["gsettings", "get", "org.gnome.desktop.interface", key],
                    capture_output=True, text=True, timeout=1).stdout.strip().lower()
                if "dark" in out:
                    dark = True
                    break
                if out:
                    dark = False
        except (OSError, subprocess.SubprocessError):
            pass
    if dark is None:                    # Windows / macOS / KDE: Qt knows the OS setting
        scheme = QGuiApplication.styleHints().colorScheme() if QGuiApplication.instance() else None
        if scheme == Qt.ColorScheme.Dark:
            dark = True
        elif scheme == Qt.ColorScheme.Light:
            dark = False
    if dark is None:
        app = QApplication.instance()
        dark = bool(app) and app.palette().window().color().lightness() < 128
    _dark_cache = (time.monotonic(), dark)
    return dark


def theme() -> dict:
    """Current tokens. Read when a window is built, so each new window follows
    the desktop's light/dark switch without a restart."""
    return DARK if is_dark() else LIGHT


def load_fonts():
    font_dir = resource_path(os.path.join("assets", "fonts"))
    for f in sorted(os.listdir(font_dir)) if os.path.isdir(font_dir) else []:
        if f.endswith(".ttf"):
            QFontDatabase.addApplicationFont(os.path.join(font_dir, f))


def sans(px: int = 14, medium: bool = False) -> QFont:
    f = QFont(SANS)
    f.setPixelSize(px)
    if medium:
        f.setWeight(QFont.Weight.Medium)
    return f


def mono(px: int = 11, caps: bool = True) -> QFont:
    """DM Mono 'instrument panel' font — ALL CAPS, tracked out — for labels and data."""
    f = QFont(MONO)
    f.setPixelSize(px)
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
    if caps:
        f.setCapitalization(QFont.Capitalization.AllUppercase)
    return f


# ── stylesheets ──────────────────────────────────────────────────────────────

def btn_css(t: dict, radius: int = BTN_H // 2, pad: int = 16) -> str:
    """Outlined button; checked = inverted (segmented-control style).
    radius BTN_H/2 = pill, 6 = technical."""
    return f"""
        QPushButton, QToolButton {{
            background: transparent; color: {t['text']};
            border: 1px solid {t['border']}; border-radius: {radius}px;
            padding: 0 {pad}px; min-height: {BTN_H}px; max-height: {BTN_H}px;
        }}
        QPushButton:hover, QToolButton:hover     {{ border-color: {t['text_sec']}; }}
        QPushButton:pressed, QToolButton:pressed {{ background: {t['surface']}; }}
        QPushButton:checked, QToolButton:checked {{
            background: {t['text']}; color: {t['bg']}; border-color: {t['text']};
        }}
    """


def primary_css(t: dict) -> str:
    return btn_css(t) + f"""
        QPushButton {{ background: {t['accent']}; color: {t['on_accent']}; border-color: {t['accent']}; }}
        QPushButton:hover   {{ border-color: {t['text']}; }}
        QPushButton:pressed {{ background: {t['text_sec']}; }}
    """


def card_css(t: dict) -> str:
    return f"""
        QWidget#card {{ background: {t['bg']}; border: 1px solid {t['border']}; border-radius: 16px; }}
        QLabel {{ color: {t['text']}; background: transparent; border: none; }}
    """


def menu_css(t: dict) -> str:
    return f"""
        QMenu {{
            background-color: {t['bg']}; color: {t['text']};
            border: 1px solid {t['border']}; border-radius: 12px; padding: 8px;
        }}
        QMenu::item {{ padding: 9px 24px 9px 12px; margin: 0 2px; border-radius: 6px; }}
        QMenu::item:selected {{ background-color: {t['text']}; color: {t['bg']}; }}
        QMenu::icon {{ padding-left: 10px; }}
        QMenu::separator {{ height: 1px; background: {t['border']}; margin: 8px 12px; }}
    """
