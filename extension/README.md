# Kapture Screenshot Helper (GNOME Shell extension)

Companion extension for the [Kapture](https://github.com/yeakiniqra/Kapture) screenshot app. GNOME 45–50.

## What it does

On GNOME Wayland, apps can only take screenshots through the XDG desktop portal, which flashes the screen and asks for permission. This extension makes Kapture's **capture shortcut** flash-free:

1. The extension registers the shortcut itself (`Main.wm.addKeybinding`, key `capture-shortcut` in `org.gnome.shell.extensions.kapture-screenshot`).
2. When the user presses it, the extension captures the stage with `Shell.Screenshot` into `~/.cache/kapture/shots/` (folder `0700`, file `0600`).
3. It opens that file with the Kapture app's desktop entry (`io.github.yeakiniqra.Kapture.desktop`), the same way any app opens a file.

## Security model

- Captures happen **only on a physical key press** of the user's chosen shortcut.
- The extension exposes **no D-Bus methods or properties**. Nothing can ask it for a screenshot. It owns the bus name `io.github.yeakiniqra.Kapture.ShellHelper` purely as a presence marker so the app knows to hand the shortcut over; no object is exported on it.
- If the Kapture app isn't installed, the extension registers no shortcut and does nothing.
- Everything is undone in `disable()`: the keybinding is removed and the bus name released.

## Settings

Kapture's Settings window writes `capture-shortcut` (`dconf write /org/gnome/shell/extensions/kapture-screenshot/capture-shortcut "['Print']"`). GNOME applies the change immediately.

## Packaging

```bash
gnome-extensions pack extension/kapture-screenshot@yeakiniqra.github.io --extra-source="$PWD/LICENSE" -o dist --force
```

Upload `dist/kapture-screenshot@yeakiniqra.github.io.shell-extension.zip` at https://extensions.gnome.org/upload/. Licensed MIT (see `LICENSE`).
