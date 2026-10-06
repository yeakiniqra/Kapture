// Kapture Screenshot Helper — GNOME Shell extension (GNOME 45+ / ESM).
//
// Companion for the Kapture screenshot app (https://github.com/yeakiniqra/Kapture).
//
// On GNOME Wayland an ordinary app can only take screenshots through the XDG
// portal, which flashes the screen and asks for permission. This extension
// lets Kapture's capture shortcut produce a flash-free screenshot instead —
// without opening that ability to anything else:
//
//   * The extension itself owns the keyboard shortcut (Main.wm.addKeybinding),
//     so a capture only ever happens when the user physically presses it.
//   * It exposes NO callable D-Bus API: no other program can trigger a capture.
//   * The image is written to the user's private cache directory (mode 0600)
//     and handed to the Kapture app through its .desktop file, like opening a
//     file with any other app.
//
// The well-known bus name below is owned purely as a presence marker so the
// Kapture app can tell the helper is active; nothing is exported on it.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const APP_ID = 'io.github.yeakiniqra.Kapture.desktop';
const PRESENCE_NAME = 'io.github.yeakiniqra.Kapture.ShellHelper';
const SHORTCUT_KEY = 'capture-shortcut';

export default class KaptureScreenshotHelper extends Extension {
    enable() {
        this._busy = false;
        // Shell's app system: no direct Gio(Unix).DesktopAppInfo access, which
        // moved namespaces in GNOME 49 — this works the same on 45–50.
        this._app = Shell.AppSystem.get_default().lookup_app(APP_ID);
        if (!this._app) {
            // Without the app there is nothing to hand screenshots to, so don't
            // grab a global shortcut from other applications.
            console.warn('Kapture Screenshot Helper: the Kapture app is not installed; shortcut not registered');
            return;
        }
        this._settings = this.getSettings();
        Main.wm.addKeybinding(
            SHORTCUT_KEY, this._settings,
            Meta.KeyBindingFlags.IGNORE_AUTOREPEAT,
            Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW | Shell.ActionMode.POPUP,
            () => this._capture());
        this._nameId = Gio.bus_own_name(
            Gio.BusType.SESSION, PRESENCE_NAME,
            Gio.BusNameOwnerFlags.NONE, null, null, null);
    }

    disable() {
        if (this._settings) {
            Main.wm.removeKeybinding(SHORTCUT_KEY);
            this._settings = null;
        }
        if (this._nameId) {
            Gio.bus_unown_name(this._nameId);
            this._nameId = 0;
        }
        this._app = null;
        this._busy = false;
    }

    _capture() {
        // One capture at a time; overlapping stage captures are pointless and
        // the only self-inflicted way to load the compositor.
        if (this._busy)
            return;
        this._busy = true;

        let file, stream;
        try {
            const dir = GLib.build_filenamev([GLib.get_user_cache_dir(), 'kapture', 'shots']);
            GLib.mkdir_with_parents(dir, 0o700);
            file = Gio.File.new_for_path(
                GLib.build_filenamev([dir, `shot-${GLib.get_real_time()}.png`]));
            stream = file.replace(null, false, Gio.FileCreateFlags.PRIVATE, null);
        } catch (e) {
            console.error(`Kapture Screenshot Helper: cannot create screenshot file: ${e.message}`);
            this._busy = false;
            return;
        }

        const shooter = new Shell.Screenshot();
        // screenshot(include_cursor, stream, callback) captures the whole stage
        // without the shutter flash (that animation is the screenshot UI's job).
        shooter.screenshot(false, stream, (_obj, res) => {
            let ok = false;
            try {
                [ok] = shooter.screenshot_finish(res);
            } catch (e) {
                console.error(`Kapture Screenshot Helper: capture failed: ${e.message}`);
            }
            try {
                stream.close(null);
            } catch (_e) {}
            this._busy = false;

            if (!ok || !this._app) {
                file.delete_async(GLib.PRIORITY_DEFAULT, null, null);
                return;
            }
            try {
                this._app.get_app_info().launch([file], global.create_app_launch_context(0, -1));
            } catch (e) {
                console.error(`Kapture Screenshot Helper: cannot open Kapture: ${e.message}`);
                file.delete_async(GLib.PRIORITY_DEFAULT, null, null);
            }
        });
    }
}
