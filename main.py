#!/usr/bin/env python3
"""Kapture launcher — the app lives in the `kapture` package (see kapture/app.py).
Kept at the repo root so PyInstaller and the GNOME shortcut command stay simple."""

from kapture.app import main

if __name__ == "__main__":
    main()
