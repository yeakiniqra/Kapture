#!/usr/bin/env bash
# build_deb.sh — Builds a .deb installer for Kapture
# Usage: bash build_deb.sh
set -euo pipefail

# ─── Config (auto-read VERSION from main.py) ──────────────────────────────────
PKG_NAME="kapture"
VERSION=$(grep -oP 'VERSION\s*=\s*"\K[^"]+' main.py)
MAINTAINER="Yeakin Iqra"
DESCRIPTION="Lightshot-style screenshot tool for Ubuntu"
ARCH=$(dpkg --print-architecture)
BINARY_SRC="dist/kapture"
ICON_SRC="assets/icon.png"
DEB_DIR="${PKG_NAME}_${VERSION}_${ARCH}"

echo "==> Kapture .deb builder"
echo "    Package : ${PKG_NAME}"
echo "    Version : ${VERSION}"
echo "    Arch    : ${ARCH}"
echo ""

# ─── Step 1: Build binary with PyInstaller ────────────────────────────────────
echo "[1/3] Building binary with PyInstaller..."
pyinstaller kapture.spec
echo ""

# ─── Step 2: Assemble package directory ───────────────────────────────────────
echo "[2/3] Assembling package structure..."
rm -rf "${DEB_DIR}"

mkdir -p "${DEB_DIR}/DEBIAN"
mkdir -p "${DEB_DIR}/usr/bin"
mkdir -p "${DEB_DIR}/usr/share/applications"
mkdir -p "${DEB_DIR}/usr/share/icons/hicolor/256x256/apps"
mkdir -p "${DEB_DIR}/usr/share/pixmaps"

# Binary
install -m 755 "${BINARY_SRC}" "${DEB_DIR}/usr/bin/${PKG_NAME}"

# Icons
install -m 644 "${ICON_SRC}" "${DEB_DIR}/usr/share/icons/hicolor/256x256/apps/${PKG_NAME}.png"
install -m 644 "${ICON_SRC}" "${DEB_DIR}/usr/share/pixmaps/${PKG_NAME}.png"

# GNOME Shell extension — flash-free, prompt-free capture on Wayland.
EXT_UUID="kapture-screenshot@yeakiniqra.github.io"
EXT_SRC="extension/${EXT_UUID}"
EXT_DEST="${DEB_DIR}/usr/share/gnome-shell/extensions/${EXT_UUID}"
mkdir -p "${EXT_DEST}"
install -m 644 "${EXT_SRC}/metadata.json" "${EXT_DEST}/metadata.json"
install -m 644 "${EXT_SRC}/extension.js"  "${EXT_DEST}/extension.js"

# Desktop entry (shows Kapture in the app menu / launcher)
cat > "${DEB_DIR}/usr/share/applications/${PKG_NAME}.desktop" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Kapture
GenericName=Screenshot Tool
Comment=Lightshot-style screenshot and annotation tool
Exec=kapture
Icon=kapture
Categories=Graphics;Utility;
Keywords=screenshot;capture;annotation;snip;
Terminal=false
StartupNotify=false
EOF

# Installed-Size (KiB) — computed from the staged tree, excluding control files.
INSTALLED_SIZE=$(du -sk --exclude=DEBIAN "${DEB_DIR}" | cut -f1)

# DEBIAN/control — package metadata
#
# Depends: Qt's xcb platform plugin dynamically loads these system libs; they are
# NOT bundled by PyInstaller, so declaring them turns a silent "could not load the
# Qt platform plugin xcb" crash into a clean apt dependency error. All ship by
# default on desktop Ubuntu.
# Recommends: native Wayland capture uses the XDG desktop portal (present by
# default on GNOME/KDE); grim is only needed on wlroots compositors. No
# gnome-screenshot/scrot — Kapture captures in-process.
cat > "${DEB_DIR}/DEBIAN/control" << EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Section: graphics
Priority: optional
Architecture: ${ARCH}
Maintainer: ${MAINTAINER}
Installed-Size: ${INSTALLED_SIZE}
Depends: libc6, libxcb-xinerama0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0, libxcb-render-util0, libxcb-cursor0, libfontconfig1, libegl1
Recommends: xdg-desktop-portal-gnome | xdg-desktop-portal | grim
Homepage: https://github.com/yeakiniqra/Kapture
Description: ${DESCRIPTION}
 Kapture is a Lightshot-style region screenshot tool for Ubuntu.
 Features annotation tools (pen, arrow, rectangle), auto clipboard copy,
 and saves annotated screenshots as PNG. Runs silently in the system tray.
 .
 Captures natively with no external screenshot tools: an instant QScreen grab
 on X11 and the XDG desktop portal on Wayland.
 .
 Activate with Print Screen or Ctrl+Shift+S.
EOF

# DEBIAN/postinst — refresh icon/desktop caches after install
cat > "${DEB_DIR}/DEBIAN/postinst" << 'POSTINST'
#!/bin/bash
set -e
if [ "$1" = "configure" ]; then
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true
    fi
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database /usr/share/applications 2>/dev/null || true
    fi
fi
POSTINST
chmod 755 "${DEB_DIR}/DEBIAN/postinst"

# DEBIAN/prerm — clean up before uninstall
#
# On full removal we also hand GNOME's screenshot keybindings back to every user
# Kapture borrowed `Print` from. Kapture stashes the originals in the user's
# config when it steals the key; here we restore them (or GNOME's factory
# defaults) from inside that user's session bus. All best-effort — never blocks
# removal.
cat > "${DEB_DIR}/DEBIAN/prerm" << 'PRERM'
#!/bin/bash
set -e
# Kill any running instance before removal/upgrade
if [ "$1" = "remove" ] || [ "$1" = "upgrade" ]; then
    pkill -x kapture 2>/dev/null || true
fi

if [ "$1" = "remove" ]; then
    for home in /home/*; do
        [ -d "$home" ] || continue
        user=$(basename "$home")
        id "$user" >/dev/null 2>&1 || continue
        backup="$home/.config/kapture/gnome_screenshot_backup.json"
        [ -f "$backup" ] || continue
        uid=$(id -u "$user")
        command -v runuser >/dev/null 2>&1 || continue
        command -v python3 >/dev/null 2>&1 || continue
        runuser -u "$user" -- env \
            DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${uid}/bus" \
            python3 - "$backup" <<'PYEOF' 2>/dev/null || true
import json, os, subprocess, sys
defaults = {
    "show-screenshot-ui": ["Print"],
    "screenshot": ["<Shift>Print"],
    "screenshot-window": ["<Alt>Print"],
}
try:
    with open(sys.argv[1]) as f:
        data = json.load(f)
    if not isinstance(data, dict) or not data:
        data = defaults
except Exception:
    data = defaults
for key, vals in data.items():
    val = "[" + ", ".join("'%s'" % v for v in vals) + "]"
    subprocess.run(["gsettings", "set",
                    "org.gnome.shell.keybindings", key, val])
try:
    os.remove(sys.argv[1])
except OSError:
    pass
PYEOF
    done
fi
PRERM
chmod 755 "${DEB_DIR}/DEBIAN/prerm"

echo ""

# ─── Step 3: Build .deb ───────────────────────────────────────────────────────
echo "[3/3] Building .deb package..."
dpkg-deb --build --root-owner-group "${DEB_DIR}"
rm -rf "${DEB_DIR}"

DEB_FILE="${DEB_DIR}.deb"
echo ""
echo "========================================="
echo "  Done!  =>  ${DEB_FILE}"
echo "========================================="
echo ""
echo "Install on this machine:"
echo "  sudo dpkg -i ${DEB_FILE}"
echo ""
echo "Or distribute the .deb — users install it the same way,"
echo "or by double-clicking it in their file manager."
