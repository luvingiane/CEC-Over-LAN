#!/usr/bin/env python3
"""Info panel for the TV screen (part of CEC-Over-LAN): what is playing, what is in front, how the
machine is doing. Opened by the action `info:show`; it closes by itself after a few seconds, or
when the action is used again.

  now playing    any MPRIS player on the session bus (browsers, AirPlay/Shairport, music players)
  in front       the focused window, as the receiver sees it
  screen         size and refresh rate of the monitor
  uptime, CPU, memory, disk, controller batteries, time

The gathering functions read /proc and /sys and take their paths as arguments, so they are
unit-tested; GTK is imported only when the window is built.
"""
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

SHOW_SECONDS = 8


# ---------------------------------------------------------------- gathering (no GTK)
def fmt_duration(seconds):
    seconds = int(seconds)
    d, rest = divmod(seconds, 86400)
    h, rest = divmod(rest, 3600)
    m = rest // 60
    return (f"{d} d " if d else "") + (f"{h} h " if d or h else "") + f"{m} min"


def uptime(path="/proc/uptime"):
    try:
        return fmt_duration(float(Path(path).read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        return None


def memory(path="/proc/meminfo"):
    """'3.2 / 15.6 GB' used / total, or None."""
    try:
        kb = {}
        for line in Path(path).read_text().splitlines():
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable"):
                kb[key] = int(rest.split()[0])
        total, avail = kb["MemTotal"], kb["MemAvailable"]
    except (OSError, ValueError, KeyError, IndexError):
        return None
    return f"{(total - avail) / 1048576:.1f} / {total / 1048576:.1f} GB"


def cpu_percent(path="/proc/stat", wait=0.25):
    """Busy share of all cores over a short sample, in percent, or None."""
    def sample():
        fields = Path(path).read_text().splitlines()[0].split()[1:]
        nums = [int(x) for x in fields]
        return sum(nums) - nums[3] - (nums[4] if len(nums) > 4 else 0), sum(nums)   # busy, total
    try:
        b1, t1 = sample()
        time.sleep(wait)
        b2, t2 = sample()
    except (OSError, ValueError, IndexError):
        return None
    return round(100.0 * (b2 - b1) / (t2 - t1)) if t2 > t1 else None


def disk(path="/"):
    try:
        u = shutil.disk_usage(path)
    except OSError:
        return None
    return f"{(u.total - u.free) / 1e9:.0f} / {u.total / 1e9:.0f} GB"


def batteries(root="/sys/class/power_supply"):
    """['pad 65% (charging)', ...]: the batteries of devices (pads, mice), not of the machine."""
    out = []
    try:
        entries = sorted(Path(root).iterdir())
    except OSError:
        return out
    for d in entries:
        try:
            if (d / "type").read_text().strip() != "Battery":
                continue
            scope = (d / "scope").read_text().strip() if (d / "scope").exists() else "Device"
            if scope != "Device":
                continue
            cap = (d / "capacity").read_text().strip()
            status = (d / "status").read_text().strip().lower() if (d / "status").exists() else ""
        except OSError:
            continue
        name = d.name.replace("ps-controller-battery-", "pad ").replace("hid-", "").split("-battery")[0]
        out.append(f"{name} {cap}%" + (f" ({status})" if status and status != "discharging" else ""))
    return out


def now_playing():
    """['Title - Artist  (player, playing)', ...] for every MPRIS player, over the session bus (Gio)."""
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
    except (ImportError, ValueError):
        return []
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        names = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames",
                              None, GLib.VariantType("(as)"), Gio.DBusCallFlags.NONE, 800, None).unpack()[0]
    except GLib.Error:
        return []
    lines = []
    for name in names:
        if not name.startswith("org.mpris.MediaPlayer2."):
            continue
        try:
            props = bus.call_sync(name, "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties", "GetAll",
                                  GLib.Variant("(s)", ("org.mpris.MediaPlayer2.Player",)),
                                  GLib.VariantType("(a{sv})"), Gio.DBusCallFlags.NONE, 800, None).unpack()[0]
        except GLib.Error:
            continue
        status = props.get("PlaybackStatus", "")
        meta = props.get("Metadata", {})
        title = meta.get("xesam:title") or ""
        artist = ", ".join(meta.get("xesam:artist") or [])
        if not title and status != "Playing":
            continue
        player = name.rsplit(".", 1)[-1]
        lines.append(f"{title or '(no title)'}" + (f" - {artist}" if artist else "") + f"  ({player}, {status.lower()})")
    return lines


def in_front():
    try:
        import tv_remote as core
        f = core.Focus._query()
    except Exception:
        return None
    if not f or not f["pid"]:
        return None
    return (f["title"] or f["class"] or f["comm"] or "").strip() or None


def gather(screen=None):
    """[(label, text), ...] in the order they are shown; entries that cannot be read are left out."""
    cpu = cpu_percent()
    rows = [("Time", datetime.now().strftime("%H:%M  %a %d %b")),
            ("Now playing", "\n".join(now_playing()) or None), ("In front", in_front()),
            ("Screen", screen), ("Uptime", uptime()), ("CPU", f"{cpu}%" if cpu is not None else None),
            ("Memory", memory()), ("Disk", disk()), ("Batteries", ", ".join(batteries()) or None)]
    return [(k, v) for k, v in rows if v]


# ---------------------------------------------------------------- the window (GTK)
CSS = b"""
window, window:backdrop { background: rgba(18,20,24,0.94); }
label, label:backdrop { color: #f2f4f8; font-size: 24px; }
label.key, label.key:backdrop { color: #8fa3c4; font-size: 20px; }
"""


def main():
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk

    win = Gtk.Window(title="CEC-Over-LAN info")
    win.set_decorated(False)
    win.set_accept_focus(False)
    win.set_focus_on_map(False)
    win.set_keep_above(True)
    win.set_skip_taskbar_hint(True)
    win.set_type_hint(Gdk.WindowTypeHint.DOCK)
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 100)
    display = Gdk.Display.get_default()
    monitor = display.get_primary_monitor() or display.get_monitor(0)
    geo = monitor.get_geometry()
    scale = monitor.get_scale_factor()
    screen = f"{geo.width * scale}x{geo.height * scale} @ {monitor.get_refresh_rate() / 1000:.0f} Hz"

    grid = Gtk.Grid(column_spacing=28, row_spacing=10)
    grid.set_margin_start(36)
    grid.set_margin_end(36)
    grid.set_margin_top(28)
    grid.set_margin_bottom(28)
    win.add(grid)

    def render():
        for child in grid.get_children():
            grid.remove(child)
        for row, (key, text) in enumerate(gather(screen)):
            k = Gtk.Label(label=key, xalign=1.0, yalign=0.0)
            k.get_style_context().add_class("key")
            v = Gtk.Label(label=text, xalign=0.0, yalign=0.0)
            v.set_line_wrap(True)
            v.set_max_width_chars(60)
            grid.attach(k, 0, row, 1, 1)
            grid.attach(v, 1, row, 1, 1)
        grid.show_all()
        size = win.get_preferred_size()[1]
        win.resize(max(size.width, 520), size.height)
        win.move(geo.x + 60, geo.y + geo.height - size.height - 60)    # bottom left, like the TV's own panel
        return True

    render()
    win.show_all()
    GLib.timeout_add_seconds(1, render)
    GLib.timeout_add_seconds(int(os.environ.get("CEC_INFO_SECONDS", SHOW_SECONDS)), Gtk.main_quit)
    Gtk.main()


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
