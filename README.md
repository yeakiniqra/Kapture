<img width="1536" height="1024" alt="header" src="https://github.com/user-attachments/assets/d83bd964-ec6c-4bb8-9fd1-9ad5480579b2" />


# Kapture — Lightshot-style screenshot tool for Ubuntu

## Installation

### For Users — install the .deb package (recommended)

Just like installing Discord or VS Code — one file, done. No Python or terminal knowledge required.

> **Compatible with:** Ubuntu 20.04 and later, on any 64-bit Intel or AMD processor (`amd64`).

> **No screenshot tools to install.** Kapture captures natively — there's nothing extra to set up. On **X11** it works out of the box; on **Wayland** it uses the built-in XDG desktop portal, which ships by default on Ubuntu's GNOME and KDE sessions.

---

**Step 1 — Download the .deb**

Go to the [Releases page](https://github.com/yeakiniqra/Kapture/releases/tag/v3.0.0) and download `kapture_3.0.0_amd64.deb`.

---

**Step 2 — Install**

**Option A — Double-click** the downloaded `.deb` file in your file manager.  
It will open in GNOME Software / GDebi. Click **Install** and enter your password.

**Option B — Terminal**

```bash
sudo dpkg -i kapture_3.0.0_amd64.deb
# if apt reports missing dependencies, pull them in with:
sudo apt -f install
```

---

**Step 3 — Launch**

Search for **Kapture** in your app launcher, or run `kapture` in a terminal.

The app starts silently in the system tray. Press `Print Screen` or `Ctrl+Shift+S` to take a screenshot — the capture is instant and uses no external tools. The capture shortcut, save folder and auto-save can all be changed from **tray → Settings**.

> **GNOME Wayland users — one-time step for flash-free capture.**
> On GNOME's Wayland session the system screenshot portal always plays a shutter flash and asks for permission — that's an OS limitation no normal app can bypass. Kapture ships a tiny **GNOME Shell extension** that captures from inside the Shell with **no flash and no prompt**. The `.deb` installs and registers it automatically; just **log out and back in once** after installing to activate it. Until then, captures fall back to the portal (with the flash). On an **X11/Xorg** session no extension is needed — capture is already instant and flash-free.

---

**Uninstall**

```bash
sudo apt remove kapture
```

---

### For Developers — run from source

**Step 1 — Clone the repository**

```bash
git clone https://github.com/yeakiniqra/Kapture.git
cd kapture
```

**Step 2 — Create and activate a virtual environment**

```bash
python3 -m venv venv
source venv/bin/activate
```

**Step 3 — Install Python dependencies**

```bash
pip install -r requirements.txt
```

> No screenshot backend to install — capture is handled in-process by Qt (X11) and the XDG desktop portal (Wayland).

**Step 4 — Run**

```bash
python3 main.py
```

> **GNOME Wayland (running from source):** the flash-free Shell extension is bundled only in the `.deb`. To get flash-free capture during development, install it into your user dir once and log out/in:
> ```bash
> cp -r "extension/kapture-screenshot@yeakiniqra.github.io" ~/.local/share/gnome-shell/extensions/
> gnome-extensions enable kapture-screenshot@yeakiniqra.github.io   # after re-login
> ```
> Without it, source runs fall back to the portal (with the flash).

---

### For Developers — build the .deb yourself

```bash
# Install packaging tools (one-time)
sudo apt install dpkg-dev

# Build .deb (runs PyInstaller then packages it)
bash build_deb.sh
```

Output: `kapture_3.0.0_amd64.deb` — ready to share or install.

## Usage

1. Press `Print Screen` or `Ctrl+Shift+S` — the screen dims and your cursor becomes a crosshair
2. Click and drag to select a region
3. Release to confirm the selection — the captured area lifts off the background with a drop shadow and the annotation toolbar appears
4. Use the annotation tools to mark up the screenshot:
   - **Pen** — freehand drawing
   - **Arrow** — draw directional arrows
   - **Box** — draw rectangles
   - **Pixelate** — drag over anything sensitive to redact it; the pixels are baked into the image so they can't be recovered
   - **Color picker** — choose pen/arrow/box color
   - **Undo / Redo** — step back and forth through your edits (`Ctrl+Z` / `Ctrl+Shift+Z`)
5. Click **Copy** to put the result on the clipboard, or **Save** to write a PNG
6. Close the editor with the **✕** in the top-right corner of the window

## Settings

Open the tray menu → **Settings** to configure:
- **Capture shortcut** — pick the global hotkey (e.g. `Print`, `Ctrl+Shift+S`)
- **Save folder** — where screenshots are written
- **Auto-save** — skip the save dialog and drop straight into the save folder

Settings persist in `~/.config/kapture/config.json`.

## Features
- Drag to select any region on screen
- Dimmed overlay with live selection size indicator and an accent glow that lifts the selection off the background
- Annotation tools: pen/marker, arrow, rectangle, **pixelate redaction**, color picker
- **Undo / redo** with full history
- Auto-copy to clipboard on selection, plus explicit **Copy** and **Save** buttons
- Save as PNG, with optional **auto-save** to a chosen folder
- **Settings window** — change the capture hotkey, save folder and auto-save without editing any files
- Close button pinned to the window's top-right corner, like any native app
- Draggable toolbar with remembered last position
- Runs silently in the system tray
- **Native capture pipeline — no external screenshot tools.** Instant `QScreen` grab on X11; on GNOME Wayland a bundled GNOME Shell extension captures **flash-free and prompt-free**, with the XDG desktop portal as the automatic fallback
- Ships a small GNOME Shell extension (`kapture-screenshot@yeakiniqra.github.io`) installed and registered by the `.deb`; activate with one log out/in

## Changelog

### v3.0.0
- **Pixelate / redaction tool** — drag to obscure sensitive areas; pixelation is committed as a baked layer so it can't be peeled back off the PNG
- **Settings window** — configure the capture shortcut, save folder and auto-save from the tray; persisted to `~/.config/kapture/config.json`
- **Undo / redo** for all annotations (`Ctrl+Z` / `Ctrl+Shift+Z`)
- **Close button moved to the window's top-right corner**, matching standard Ubuntu window controls
- **Selection drop shadow / accent glow** so the captured region stands out from the dimmed background
- **Print Screen fix** — Kapture now claims the `Print Screen` key instead of letting it trigger GNOME's built-in screenshot, via a single-instance IPC trigger and GNOME custom keybinding

### v2.0.0
- **Native capture pipeline** — removed the dependency on `gnome-screenshot` / `scrot`; capture is now an in-process `QScreen` grab on X11 and the XDG desktop portal on Wayland
- **Flash-free, prompt-free Wayland capture** via a bundled GNOME Shell extension that runs inside the Shell's privileged context
- Modernised annotation editor, About dialog and system-tray menu/icon
- Added a **Copy** button alongside Save
- `.deb` installs and registers the Shell extension automatically

### v1.0.0
- Initial release — region selection, pen/arrow/rectangle annotation, clipboard copy, PNG save, system-tray operation

## Contributing

Contributions are welcome! Feel free to open issues or submit pull requests.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -m 'Add my feature'`)
4. Push to the branch (`git push origin feature/my-feature`)
5. [Open a Pull Request](https://github.com/yeakiniqra/Kapture/pulls)

## License

This project is licensed under the **MIT License**.

```
MIT License

Copyright (c) 2026 Kapture Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
