// Kapture Screenshot Helper — GNOME Shell extension (GNOME 45+ / ESM).
//
// Exposes a tiny D-Bus service, org.kapture.ScreenshotHelper, that captures the
// whole stage to a PNG file using Shell.Screenshot.  Because this runs INSIDE
// the Shell's privileged process, Shell.Screenshot is permitted (unlike the
// org.gnome.Shell.Screenshot D-Bus API, which Mutter refuses for external apps),
// and we never invoke the shutter-flash UI — so the capture is flash-free and
// needs no portal consent prompt.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const IFACE = `
<node>
  <interface name="org.kapture.ScreenshotHelper">
    <method name="CaptureToFile">
      <arg type="s" direction="in"  name="filename"/>
      <arg type="b" direction="out" name="success"/>
    </method>
  </interface>
</node>`;

const OBJECT_PATH = '/org/kapture/ScreenshotHelper';
const BUS_NAME    = 'org.kapture.ScreenshotHelper';

export default class KaptureScreenshotHelper extends Extension {
    enable() {
        this._busy = false;
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._nameId = Gio.bus_own_name(
            Gio.BusType.SESSION,
            BUS_NAME,
            Gio.BusNameOwnerFlags.REPLACE,
            null, null, null);
    }

    disable() {
        this._busy = false;
        if (this._nameId) {
            Gio.bus_unown_name(this._nameId);
            this._nameId = null;
        }
        if (this._dbus) {
            this._dbus.unexport();
            this._dbus = null;
        }
    }

    // Async D-Bus method (the trailing "Async" + (params, invocation) signature
    // tells GJS to complete the call later, after the screenshot finishes).
    CaptureToFileAsync([filename], invocation) {
        const finish = ok =>
            invocation.return_value(new GLib.Variant('(b)', [!!ok]));

        // Never run two captures concurrently — overlapping Shell.Screenshot
        // calls against the live stage are the one self-inflicted way this could
        // destabilise the compositor.  Kapture already serialises requests, but
        // we self-guard regardless.
        if (this._busy) {
            finish(false);
            return;
        }
        this._busy = true;
        const done = ok => { this._busy = false; finish(ok); };

        try {
            const file = Gio.File.new_for_path(filename);
            const stream = file.replace(
                null, false, Gio.FileCreateFlags.NONE, null);

            const shooter = new Shell.Screenshot();
            // screenshot(include_cursor, stream, callback): captures the whole
            // stage. No _flashArea() call here -> no shutter flash.
            shooter.screenshot(false, stream, (obj, res) => {
                let ok = false;
                try {
                    [ok] = shooter.screenshot_finish(res);
                } catch (e) {
                    logError(e, 'Kapture: screenshot_finish failed');
                }
                try { stream.close(null); } catch (e) {}
                done(ok);
            });
        } catch (e) {
            logError(e, 'Kapture: CaptureToFile failed');
            done(false);
        }
    }
}
