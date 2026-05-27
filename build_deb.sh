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
StartupNotify=false
EOF

# DEBIAN/control — package metadata
cat > "${DEB_DIR}/DEBIAN/control" << EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Section: graphics
Priority: optional
Architecture: ${ARCH}
Recommends: gnome-screenshot | scrot
Maintainer: ${MAINTAINER}
Description: ${DESCRIPTION}
 Kapture is a Lightshot-style region screenshot tool for Ubuntu.
 Features annotation tools (pen, arrow, rectangle), auto clipboard copy,
 and saves annotated screenshots as PNG. Runs silently in the system tray.
 .
 Activate with Print Screen or Ctrl+Shift+S.
EOF

# DEBIAN/postinst — refresh icon/desktop caches after install
cat > "${DEB_DIR}/DEBIAN/postinst" << 'POSTINST'
#!/bin/bash
set -e
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications 2>/dev/null || true
fi
POSTINST
chmod 755 "${DEB_DIR}/DEBIAN/postinst"

# DEBIAN/prerm — clean up before uninstall
cat > "${DEB_DIR}/DEBIAN/prerm" << 'PRERM'
#!/bin/bash
set -e
# Kill any running instance before uninstalling
pkill -x kapture 2>/dev/null || true
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
