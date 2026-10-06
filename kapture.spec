# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Kapture — one self-contained binary: dist/kapture (Linux)
# or dist/kapture.exe (Windows; renamed with the version by the CI workflow).
#
# Bundles the assets folder (fonts, icons, logo) and pynput's dynamically-imported
# platform backends (global hotkeys), which PyInstaller's static analysis misses.
# The `kapture` package itself is found through main.py's imports.

import sys

block_cipher = None

if sys.platform == 'win32':
    pynput_backends = ['pynput.keyboard._win32', 'pynput.mouse._win32']
else:
    pynput_backends = ['pynput.keyboard._xorg', 'pynput.mouse._xorg',
                       'pynput.keyboard._uinput', 'pynput.mouse._uinput']

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets')],
    hiddenimports=pynput_backends,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide2'],   # never bundle a second Qt
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='kapture',
    icon='assets/app.ico',    # used on Windows; ignored on Linux
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,            # GUI/tray app — no terminal window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
