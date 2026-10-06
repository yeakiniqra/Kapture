"""About, one-time helper setup, Settings, and the capture-failure message."""

import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel,
                               QMessageBox, QVBoxLayout)

from kapture import APP_NAME, AUTHOR, VERSION, icons, windows
from kapture.config import CONFIG, SHORTCUTS, WINDOWS, binding_label, resource_path, save_config
from kapture.theme import BTN_H, MONO, btn_css, mono, primary_css, theme
from kapture.ui.widgets import CardDialog, button, label


class AboutDialog(CardDialog):
    def __init__(self, parent=None):
        super().__init__(400, parent)

    def build(self):
        t, v = self.t, self.body

        # Header: logo + name (primary) + version (tertiary)
        header = QHBoxLayout()
        header.setSpacing(16)
        logo = QLabel()
        logo.setPixmap(QIcon(resource_path(os.path.join("assets", "app-logo.png"))).pixmap(56, 56))
        header.addWidget(logo, 0, Qt.AlignmentFlag.AlignTop)
        name = QLabel(APP_NAME)
        name.setStyleSheet("font-size: 32px; font-weight: 500;")
        titlebox = QVBoxLayout()
        titlebox.setSpacing(4)
        titlebox.addWidget(name)
        titlebox.addWidget(label(f"Version {VERSION}", t))
        header.addLayout(titlebox)
        header.addStretch()
        v.addLayout(header)
        v.addSpacing(24)

        desc = QLabel("A Lightshot-style screenshot tool for Linux — capture any "
                      "region, annotate, and share in seconds.")
        desc.setWordWrap(True)
        v.addWidget(desc)
        v.addSpacing(24)

        # Stat rows: label left, value right
        for key, val in (("Shortcut", binding_label()), ("Made by", AUTHOR), ("License", "MIT")):
            row = QHBoxLayout()
            row.addWidget(label(key, t))
            row.addStretch()
            row.addWidget(label(val, t, "text"))
            v.addLayout(row)
            v.addSpacing(8)
        v.addSpacing(24)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        gh = button("GitHub", btn_css(t), t, "external-link")
        gh.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("https://github.com/yeakiniqra/Kapture")))
        btns.addWidget(gh)
        btns.addStretch()
        ok = button("Close", primary_css(t))
        ok.clicked.connect(self.accept)
        ok.setDefault(True)
        btns.addWidget(ok)
        v.addLayout(btns)


class HelperSetupDialog(CardDialog):
    """One-time, OPTIONAL prompt when the flash-free Shell helper is installed but
    not loaded yet. Kapture already works (via the portal); this just offers to
    finish the upgrade now instead of waiting for the next natural login."""

    def __init__(self, parent=None):
        self.logout = False
        super().__init__(430, parent)

    def build(self):
        t, v = self.t, self.body
        v.addWidget(label("Setup", t))
        v.addSpacing(8)
        title = QLabel("One step left for flash-free capture")
        title.setStyleSheet("font-size: 20px; font-weight: 500;")
        title.setWordWrap(True)
        v.addWidget(title)
        v.addSpacing(16)

        body = QLabel(
            "Kapture is ready to use right now — captures work immediately.\n\n"
            "To make them flash-free (and skip GNOME's permission prompt), a small "
            "screenshot helper was installed. GNOME loads it automatically the next "
            "time you log in — then it just works, every session, forever.")
        body.setWordWrap(True)
        v.addWidget(body)
        v.addSpacing(16)

        hint = QLabel("No rush — keep using Kapture as-is, or log out now to switch "
                      "it on immediately.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {t['text_sec']};")
        v.addWidget(hint)
        v.addSpacing(32)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        btns.addStretch()
        later = button("Maybe later", btn_css(t))
        later.clicked.connect(self.reject)
        btns.addWidget(later)
        out = button("Log out now", primary_css(t), t, "power")
        out.clicked.connect(self._choose_logout)
        btns.addWidget(out)
        v.addLayout(btns)

    def _choose_logout(self):
        self.logout = True
        self.accept()


class SettingsDialog(CardDialog):
    """Capture shortcut, save folder, and auto-save."""

    def __init__(self, apply_cb=None, parent=None):
        self._apply_cb = apply_cb
        self._save_dir = CONFIG.get("save_dir") or os.path.expanduser("~/Pictures")
        super().__init__(440, parent)

    def build(self):
        t, v = self.t, self.body
        title = QLabel("Settings")
        title.setStyleSheet("font-size: 20px; font-weight: 500;")
        v.addWidget(title)
        v.addSpacing(32)

        # Capture shortcut
        v.addWidget(label("Capture shortcut", t))
        v.addSpacing(8)
        self.combo = QComboBox()
        self.combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.combo.setFont(mono(12))
        self.combo.setStyleSheet(f"""
            QComboBox {{
                background: {t['surface']}; color: {t['text']};
                border: 1px solid {t['border']}; border-radius: 6px;
                padding: 0 12px; min-height: {BTN_H}px;
            }}
            QComboBox:hover {{ border-color: {t['text_sec']}; }}
            QComboBox::drop-down {{ border: none; width: 32px; }}
            QComboBox::down-arrow {{
                image: url({icons.file("chevron-down", t["text_sec"], 16)}); width: 16px; height: 16px;
            }}
            QComboBox QAbstractItemView {{
                background: {t['bg']}; color: {t['text']}; border: 1px solid {t['border']};
                selection-background-color: {t['text']}; selection-color: {t['bg']};
                outline: none;
            }}
        """)
        for text, _binding in SHORTCUTS:
            self.combo.addItem(text)
        cur = CONFIG.get("capture_binding", "<Control><Shift>s")
        self.combo.setCurrentIndex(
            next((i for i, (_t, b) in enumerate(SHORTCUTS) if b == cur), 1))
        v.addWidget(self.combo)
        v.addSpacing(8)

        hint = QLabel("Choosing “Print Screen” turns off Windows’ own Print Screen "
                      "handler (Snipping Tool). It’s restored if you switch back."
                      if WINDOWS else
                      "Choosing “Print Screen” replaces GNOME’s built-in "
                      "screenshot shortcut. It’s restored if you switch back.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {t['text_sec']}; font-size: 12px;")
        v.addWidget(hint)
        v.addSpacing(24)

        # Save folder
        v.addWidget(label("Save folder", t))
        v.addSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.dir_lbl = QLabel(self._save_dir)
        self.dir_lbl.setFont(mono(12, caps=False))
        self.dir_lbl.setStyleSheet(
            f"background: {t['surface']}; border: 1px solid {t['border']};"
            f"border-radius: 6px; padding: 0 12px; min-height: {BTN_H}px; color: {t['text']};")
        self.dir_lbl.setMinimumWidth(250)
        row.addWidget(self.dir_lbl, 1)
        browse = button("Browse", btn_css(t, 6, 0), t, "folder", square=True)
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        v.addLayout(row)
        v.addSpacing(24)

        # Auto-save (+ autostart on Windows; the .deb handles it on Linux)
        self.autosave = _checkbox("Auto-save (skip the file dialog)", bool(CONFIG.get("auto_save")), t)
        v.addWidget(self.autosave)
        self.autostart = None
        if WINDOWS:
            v.addSpacing(12)
            self.autostart = _checkbox("Start with Windows", windows.autostart_enabled(), t)
            v.addWidget(self.autostart)
        v.addSpacing(32)

        btns = QHBoxLayout()
        btns.addStretch()
        btns.setSpacing(8)
        cancel = button("Cancel", btn_css(t))
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        save = button("Save", primary_css(t))
        save.clicked.connect(self._apply)
        btns.addWidget(save)
        v.addLayout(btns)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose save folder", self._save_dir)
        if d:
            self._save_dir = d
            self.dir_lbl.setText(d)

    def _apply(self):
        CONFIG["save_dir"] = self._save_dir
        CONFIG["auto_save"] = self.autosave.isChecked()
        binding = SHORTCUTS[self.combo.currentIndex()][1]
        CONFIG["capture_binding"] = binding
        if self.autostart is not None:
            CONFIG["autostart"] = self.autostart.isChecked()
            windows.set_autostart(CONFIG["autostart"])
        save_config(CONFIG)
        if self._apply_cb:
            self._apply_cb(binding)
        self.accept()


def _checkbox(text: str, checked: bool, t: dict) -> QCheckBox:
    box = QCheckBox(text)
    box.setChecked(checked)
    box.setCursor(Qt.CursorShape.PointingHandCursor)
    box.setStyleSheet(f"""
        QCheckBox {{ color: {t['text']}; spacing: 10px; }}
        QCheckBox::indicator {{
            width: 18px; height: 18px; border-radius: 4px;
            border: 1px solid {t['border']}; background: transparent;
        }}
        QCheckBox::indicator:hover   {{ border-color: {t['text_sec']}; }}
        QCheckBox::indicator:checked {{ background: {t['text']}; border-color: {t['text']}; }}
    """)
    return box


def show_capture_error():
    """Shown only when a capture genuinely fails — never a silent black PNG."""
    t = theme()
    dlg = QMessageBox()
    dlg.setWindowTitle(f"{APP_NAME} — Capture Failed")
    dlg.setIcon(QMessageBox.Icon.Warning)
    dlg.setTextFormat(Qt.TextFormat.RichText)
    if WINDOWS:
        dlg.setText("<b>Couldn't capture the screen.</b><br><br>"
                    "Another app may be blocking screen capture (some games and "
                    "DRM-protected video do this). Try again, or check the log in "
                    "<code>%LOCALAPPDATA%\\Kapture\\cache</code>.")
    else:
        dlg.setText(
            "<b>Couldn't capture the screen.</b><br><br>"
            "Kapture captures natively (no external tools needed). On X11 this "
            "always works; on Wayland it uses the desktop portal.<br><br>"
            "If you're on a Wayland session where the portal is unavailable, "
            "either log in with an <b>Xorg</b> session, or on a wlroots compositor "
            "(Sway/Hyprland) install <code>grim</code>:")
        dlg.setDetailedText(
            "Wayland portal backend (present by default on Ubuntu GNOME/KDE):\n"
            "  sudo apt install xdg-desktop-portal-gnome\n\n"
            "wlroots compositors (Sway, Hyprland):\n"
            "  sudo apt install grim\n\n"
            "Or pick 'Ubuntu on Xorg' from the login screen gear menu.")
    dlg.setStyleSheet(btn_css(t, 6) + f"""
        QMessageBox {{ background-color: {t['bg']}; }}
        QLabel {{ color: {t['text']}; }}
        QTextEdit {{
            background: {t['surface']}; color: {t['text']};
            border: 1px solid {t['border']}; border-radius: 6px;
            font-family: "{MONO}"; font-size: 12px;
        }}
    """)
    dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
    dlg.exec()
