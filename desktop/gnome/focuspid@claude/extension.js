import Gio from 'gi://Gio';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const IFACE = `<node>
  <interface name="org.claude.FocusPid">
    <method name="Get">
      <arg type="i" direction="out" name="pid"/>
      <arg type="s" direction="out" name="wm_class"/>
      <arg type="s" direction="out" name="app_id"/>
      <arg type="s" direction="out" name="title"/>
    </method>
    <method name="ActivatePid">
      <arg type="i" direction="in" name="pid"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
  </interface>
</node>`;

export default class FocusPidExtension extends Extension {
    enable() {
        this._impl = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._impl.export(Gio.DBus.session, '/org/claude/FocusPid');
        this._owner = Gio.bus_own_name_on_connection(
            Gio.DBus.session, 'org.claude.FocusPid',
            Gio.BusNameOwnerFlags.NONE, null, null);
    }

    disable() {
        if (this._owner) { Gio.bus_unown_name(this._owner); this._owner = 0; }
        if (this._impl) { this._impl.unexport(); this._impl = null; }
    }

    Get() {
        const w = global.display.focus_window;
        if (!w)
            return [0, '', '', ''];
        return [w.get_pid(), w.get_wm_class() || '',
                w.get_gtk_application_id() || '', w.get_title() || ''];
    }

    ActivatePid(pid) {
        for (const actor of global.get_window_actors()) {
            const w = actor.meta_window;
            if (w && w.get_pid() === pid) {
                w.activate(global.get_current_time());
                return true;
            }
        }
        return false;
    }
}
