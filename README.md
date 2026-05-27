# Kapture — Lightshot-style screenshot tool for Ubuntu (PyQt5)

## Install dependencies

```bash
pip install PyQt5 pynput qasync
```

Also requires one of the following to be installed on your system for accurate screen capture:

```bash
sudo apt install gnome-screenshot   # preferred
sudo apt install scrot               # fallback
```

## Run

```bash
python3 screenshot_tool.py
```

The app runs in the system tray. Press `Ctrl+Shift+S` to start a capture.

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

Edit this line in `screenshot_tool.py`:
```python
HOTKEY = "<ctrl>+<shift>+s"
```

Other examples:
- `"<print_screen>"` — Print Screen key
- `"<ctrl>+<alt>+s"` — Ctrl+Alt+S

## Auto-start on login

```bash
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/kapture.desktop << EOF
[Desktop Entry]
Type=Application
Name=Kapture
Exec=python3 /path/to/screenshot_tool.py
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF
```

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
