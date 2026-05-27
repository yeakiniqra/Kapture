# Kapture — Lightshot-style screenshot tool for Ubuntu

## Installation

### For Users — install the .deb package (recommended)

Just like installing Discord or VS Code — one file, done. No Python or terminal knowledge required.

> **Compatible with:** Ubuntu 20.04 and later, on any 64-bit Intel or AMD processor (`amd64`).

---

**Step 1 — Install a screen capture backend**

Kapture needs one of these system tools to take screenshots. Open a terminal and run:

```bash
sudo apt install gnome-screenshot
```

If that's not available, use the fallback:

```bash
sudo apt install scrot
```

> You only need one. `gnome-screenshot` is preferred — it gives the most accurate results.

---

**Step 2 — Download the .deb**

Go to the [Releases page](https://github.com/yeakin-iqra/kapture/releases) and download the latest `kapture_X.X.X_amd64.deb` file.

---

**Step 3 — Install**

**Option A — Double-click** the downloaded `.deb` file in your file manager.  
It will open in GNOME Software / GDebi. Click **Install** and enter your password.

**Option B — Terminal**

```bash
sudo dpkg -i kapture_1.0.0_amd64.deb
```

---

**Step 4 — Launch**

Search for **Kapture** in your app launcher, or run `kapture` in a terminal.

The app starts silently in the system tray. Press `Print Screen` or `Ctrl+Shift+S` to take a screenshot.

---

**Uninstall**

```bash
sudo apt remove kapture
```

---

### For Developers — run from source

**Step 1 — Clone the repository**

```bash
git clone https://github.com/yeakin-iqra/kapture.git
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

**Step 4 — Install a screen capture backend**

```bash
sudo apt install gnome-screenshot   # preferred
# or
sudo apt install scrot               # fallback
```

**Step 5 — Run**

```bash
python3 main.py
```

---

### For Developers — build the .deb yourself

```bash
# Install packaging tools (one-time)
sudo apt install dpkg-dev

# Build .deb (runs PyInstaller then packages it)
bash build_deb.sh
```

Output: `kapture_1.0.0_amd64.deb` — ready to share or install.

## Usage

1. Press `Ctrl+Shift+S` — the screen dims and your cursor becomes a crosshair
2. Click and drag to select a region
3. Release to confirm the selection — the annotation toolbar appears
4. Use the annotation tools to mark up the screenshot:
   - **Pen** — freehand drawing
   - **Arrow** — draw directional arrows
   - **Box** — draw rectangles
   - **Color picker** — choose pen/arrow/box color
5. Click **Save** to save the annotated screenshot as a PNG
6. The captured region is automatically copied to the clipboard on drag release

## Change hotkey

Edit the `HOTKEYS` list near the top of `main.py`:
```python
HOTKEYS = ["<print_screen>", "<ctrl>+<shift>+s"]
```

Other examples:
- `"<print_screen>"` — Print Screen key
- `"<ctrl>+<alt>+s"` — Ctrl+Alt+S

## Features
- Drag to select any region on screen
- Dimmed overlay with live selection size indicator
- Annotation tools: pen/marker, arrow, rectangle, color picker
- Auto-copy to clipboard on selection
- Save annotated screenshot as PNG
- Draggable toolbar with remembered last position
- Runs silently in the system tray
- asyncio-based capture using gnome-screenshot or scrot (no overlay darkening)

## Contributing

Contributions are welcome! Feel free to open issues or submit pull requests.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -m 'Add my feature'`)
4. Push to the branch (`git push origin feature/my-feature`)
5. Open a Pull Request

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
