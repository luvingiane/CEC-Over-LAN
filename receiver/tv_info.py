#!/usr/bin/env python3
"""Info panel for the TV screen (part of CEC-Over-LAN), as wide as the screen. Opened by the action
`info:show`; it closes by itself after a few seconds, or when the action is used again.

  left     what is playing (any MPRIS player: browsers, AirPlay/Shairport, music apps): artwork, title,
           artist, album, progress, the player's icon, and a spectrum of what the machine is playing
  right    the window in front (with its icon), screen mode, uptime, CPU, memory and disk as bars,
           the batteries of pads, the time

The gathering functions read /proc and /sys and take their paths as arguments, so they are
unit-tested; GTK, numpy and pw-record are used only when the window is built (without numpy or
pw-record the spectrum stays flat).
"""
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
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


def players():
    """Every MPRIS player with something to show, over the session bus (Gio):
    [{name, status, title, artist, album, art, length, position, entry}], playing ones first."""
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        names = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames",
                              None, GLib.VariantType("(as)"), Gio.DBusCallFlags.NONE, 800, None).unpack()[0]
    except Exception:
        return []

    def get_all(name, path, iface):
        return bus.call_sync(name, path, "org.freedesktop.DBus.Properties", "GetAll", GLib.Variant("(s)", (iface,)),
                             GLib.VariantType("(a{sv})"), Gio.DBusCallFlags.NONE, 800, None).unpack()[0]

    found = []
    for name in names:
        if not name.startswith("org.mpris.MediaPlayer2."):
            continue
        try:
            props = get_all(name, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player")
            root = get_all(name, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2")
        except Exception:
            continue
        meta, status = props.get("Metadata", {}), props.get("PlaybackStatus", "")
        title = meta.get("xesam:title") or ""
        if not title and status != "Playing":
            continue
        found.append({"name": root.get("Identity") or name.rsplit(".", 1)[-1], "status": status,
                      "title": title or "(no title)", "artist": ", ".join(meta.get("xesam:artist") or []),
                      "album": meta.get("xesam:album") or "", "art": meta.get("mpris:artUrl") or "",
                      "length": int(meta.get("mpris:length") or 0), "position": int(props.get("Position") or 0),
                      "entry": root.get("DesktopEntry") or ""})
    found.sort(key=lambda p: p["status"] != "Playing")
    return found


def screen_mode():
    """'2560x1440 @ 60 Hz': the mode the compositor really drives (Mutter), or None. GTK reports a
    scaled size under Xwayland, which is why it is not used."""
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        state = bus.call_sync("org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig",
                              "org.gnome.Mutter.DisplayConfig", "GetCurrentState", None, None,
                              Gio.DBusCallFlags.NONE, 800, None).unpack()
        for _monitor, modes, _props in state[1]:
            for _id, w, h, refresh, *_rest, props in modes:
                if props.get("is-current"):
                    return f"{w}x{h} @ {round(refresh)} Hz"
    except Exception:
        return None
    return None


def in_front():
    """{'title', 'ids'} of the focused window, or None; 'ids' are names to look the app's icon up by."""
    try:
        import tv_remote as core
        f = core.Focus._query()
    except Exception:
        return None
    if not f or not f["pid"]:
        return None
    ids = [x for x in (f["app_id"], f["class"], f["comm"]) if x]
    return {"title": (f["title"] or f["class"] or f["comm"] or "").strip(), "ids": ids}


def snapshot(screen=None):
    """Everything the panel shows, as plain data (the window only draws it)."""
    pls = players()
    return {"time": datetime.now().strftime("%H:%M"), "date": datetime.now().strftime("%a %d %b"),
            "player": pls[0] if pls else None, "front": in_front(), "screen": screen or screen_mode(),
            "uptime": uptime(), "cpu": cpu_percent(), "mem": memory_fraction(), "mem_text": memory(),
            "disk": disk_fraction(), "disk_text": disk(), "batteries": batteries()}


def memory_fraction(path="/proc/meminfo"):
    try:
        kb = {}
        for line in Path(path).read_text().splitlines():
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable"):
                kb[key] = int(rest.split()[0])
        return 1 - kb["MemAvailable"] / kb["MemTotal"]
    except (OSError, ValueError, KeyError, IndexError, ZeroDivisionError):
        return None


def disk_fraction(path="/"):
    try:
        u = shutil.disk_usage(path)
        return (u.total - u.free) / u.total
    except (OSError, ZeroDivisionError):
        return None


# ---------------------------------------------------------------- spectrum and artwork (no GTK)
BANDS = 48


def band_edges(rate=44100, n=1024, bands=BANDS, lo=40.0, hi=16000.0):
    """FFT bin boundaries of `bands` log-spaced bands between lo and hi Hz."""
    import numpy as np
    freqs = np.geomspace(lo, hi, bands + 1)
    bins = np.unique(np.clip((freqs * n / rate).astype(int), 1, n // 2))
    return bins


def levels_from_samples(samples, edges, previous=None):
    """0..1 level per band from int16 samples; `previous` makes the bars fall smoothly."""
    import numpy as np
    x = samples.astype(np.float32) / 32768.0
    mag = np.abs(np.fft.rfft(x * np.hanning(len(x)))) / len(x)
    out = []
    for i in range(len(edges) - 1):
        m = float(mag[edges[i]:max(edges[i] + 1, edges[i + 1])].max())
        db = 20 * np.log10(m + 1e-9)
        out.append(min(1.0, max(0.0, (db + 78.0) / 60.0)))
    if previous:
        out = [max(v, p * 0.82) for v, p in zip(out, previous)]
    return out


class Spectrum(threading.Thread):
    """What the machine is playing, as band levels: pw-record on the monitor of the output, numpy FFT."""
    N = 1024

    def __init__(self):
        super().__init__(daemon=True)
        self.levels = [0.0] * BANDS
        self.proc = None
        self.stopped = False

    def run(self):
        try:
            import numpy as np
            edges = band_edges(n=self.N)
            self.proc = subprocess.Popen(
                ["pw-record", "-a", "-P", "stream.capture.sink=true", "--rate", "44100", "--channels", "1",
                 "--format", "s16", "--latency", "20ms", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            while not self.stopped:
                raw = self.proc.stdout.read(self.N * 2)
                if len(raw) < self.N * 2:
                    break
                self.levels = levels_from_samples(np.frombuffer(raw, dtype="<i2"), edges, self.levels)
        except Exception:
            pass                       # no numpy or no pw-record: the bars stay flat

    def stop(self):
        self.stopped = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


ART_MAX_BYTES = 3_000_000


def fetch_art(url):
    """Bytes of the artwork a player points to (file://, http, https), or None. Small and with a timeout."""
    if url.startswith("file://"):
        path = Path(urllib.request.url2pathname(url[7:]))
        return path.read_bytes() if path.is_file() and path.stat().st_size < ART_MAX_BYTES else None
    if url.startswith(("http://", "https://")):
        with urllib.request.urlopen(url, timeout=3) as r:
            data = r.read(ART_MAX_BYTES + 1)
        return data if len(data) <= ART_MAX_BYTES else None
    return None


# ---------------------------------------------------------------- the window (GTK)
HEIGHT = 340
CSS = b"""
window, window:backdrop { background: rgba(14,16,20,0.96); }
label, label:backdrop { color: #f2f4f8; font-size: 22px; }
label.title, label.title:backdrop { font-size: 40px; font-weight: bold; }
label.artist, label.artist:backdrop { font-size: 28px; color: #c9d3e6; }
label.muted, label.muted:backdrop { font-size: 20px; color: #8fa3c4; }
label.clock, label.clock:backdrop { font-size: 52px; font-weight: bold; }
levelbar trough, levelbar trough:backdrop { background: #2a2f38; min-height: 10px; border-radius: 5px; }
levelbar block.filled, levelbar block.filled:backdrop { background: #3d7bff; border-radius: 5px; }
levelbar block.high, levelbar block.high:backdrop { background: #e8a33d; border-radius: 5px; }
image, image:backdrop { color: #8fa3c4; }
"""


def main():
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk
    import cairo   # noqa: F401  (pycairo: the drawing areas)

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
    screen_text = screen_mode() or f"{geo.width}x{geo.height} @ {monitor.get_refresh_rate() / 1000:.0f} Hz"

    spectrum = Spectrum()
    spectrum.start()
    state = {"data": None, "art": None, "art_url": None}

    def label(text="", css=None, xalign=0.0, ellipsize=True):
        w = Gtk.Label(label=text, xalign=xalign)
        if css:
            w.get_style_context().add_class(css)
        if ellipsize:
            w.set_ellipsize(3)           # Pango.EllipsizeMode.END
        return w

    def icon_for(ids, fallback):
        for i in ids:
            for cand in (i + ".desktop", i.lower() + ".desktop"):
                try:
                    info = Gio.DesktopAppInfo.new(cand)      # PyGObject raises when there is no such file
                except TypeError:
                    continue
                if info is not None and info.get_icon() is not None:
                    return info.get_icon()
        return Gio.ThemedIcon.new(fallback)

    # ---- left: artwork, text, progress, spectrum
    art_area = Gtk.DrawingArea()
    art_area.set_size_request(240, 240)

    def draw_art(widget, cr):
        w, h = widget.get_allocated_width(), widget.get_allocated_height()
        r = 18
        cr.new_sub_path()
        for cx, cy, a in ((w - r, r, -90), (w - r, h - r, 0), (r, h - r, 90), (r, r, 180)):
            cr.arc(cx, cy, r, a * 3.14159 / 180, (a + 90) * 3.14159 / 180)
        cr.close_path()
        cr.clip()
        pix = state["art"]
        if pix is not None:
            scaled = pix.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)
            Gdk.cairo_set_source_pixbuf(cr, scaled, 0, 0)
            cr.paint()
        else:
            cr.set_source_rgb(0.16, 0.18, 0.22)
            cr.paint()
            try:
                note = Gtk.IconTheme.get_default().load_icon("audio-x-generic-symbolic", 96, 0)
                Gdk.cairo_set_source_pixbuf(cr, note, (w - note.get_width()) / 2, (h - note.get_height()) / 2)
                cr.paint_with_alpha(0.35)
            except GLib.Error:
                pass
        return False
    art_area.connect("draw", draw_art)

    player_icon = Gtk.Image()
    player_name = label("", "muted")
    title, artist, album = label("Nothing playing", "title"), label("", "artist"), label("", "muted")
    elapsed, total = label("", "muted"), label("", "muted", 1.0)
    progress = Gtk.LevelBar()
    progress.set_min_value(0)
    progress.set_max_value(1)
    progress.set_no_show_all(True)

    bars = Gtk.DrawingArea()
    bars.set_size_request(-1, 90)
    bars.set_hexpand(True)

    def draw_bars(widget, cr):
        w, h = widget.get_allocated_width(), widget.get_allocated_height()
        lv = spectrum.levels
        gap = 6
        bw = (w - gap * (len(lv) - 1)) / len(lv)
        for i, v in enumerate(lv):
            bh = max(4.0, v * h)
            x = i * (bw + gap)
            cr.set_source_rgba(0.24, 0.48, 1.0, 0.55 + 0.45 * v)
            cr.rectangle(x, h - bh, bw, bh)
            cr.fill()
        return False
    bars.connect("draw", draw_bars)

    head = Gtk.Box(spacing=10)
    head.pack_start(player_icon, False, False, 0)
    head.pack_start(player_name, True, True, 0)
    times = Gtk.Box()
    times.pack_start(elapsed, False, False, 0)
    times.pack_end(total, False, False, 0)
    times.set_no_show_all(True)
    mid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    for w in (head, title, artist, album, times, progress, bars):
        mid.pack_start(w, False, False, 0)
    mid.set_hexpand(True)
    mid.set_vexpand(False)

    # ---- right: the machine
    clock, date = label("", "clock", 1.0), label("", "muted", 1.0)
    rows = {}

    def row(key, icon_name):
        img = Gtk.Image.new_from_gicon(Gio.ThemedIcon.new(icon_name), Gtk.IconSize.DND)
        img.set_pixel_size(30)
        text = label("", None, 0.0)
        bar = Gtk.LevelBar()
        bar.set_min_value(0)
        bar.set_max_value(1)
        bar.set_size_request(120, 10)
        bar.set_valign(Gtk.Align.CENTER)
        bar.set_no_show_all(True)
        rows[key] = (img, text, bar)
        box = Gtk.Box(spacing=12)
        box.pack_start(img, False, False, 0)
        box.pack_start(text, True, True, 0)
        box.pack_end(bar, False, False, 0)
        return box

    side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    side.set_size_request(520, -1)
    side.pack_start(clock, False, False, 0)
    side.pack_start(date, False, False, 0)
    for key, icon_name in (("front", "application-x-executable-symbolic"), ("screen", "video-display-symbolic"),
                           ("uptime", "preferences-system-time-symbolic"), ("cpu", "utilities-system-monitor-symbolic"),
                           ("mem", "media-flash-symbolic"), ("disk", "drive-harddisk-symbolic"),
                           ("bat", "battery-good-symbolic")):
        side.pack_start(row(key, icon_name), False, False, 0)

    outer = Gtk.Box(spacing=36)
    for m in ("start", "end"):
        getattr(outer, "set_margin_" + m)(56)
    outer.set_margin_top(28)
    outer.set_margin_bottom(28)
    outer.pack_start(art_area, False, False, 0)
    outer.pack_start(mid, True, True, 0)
    outer.pack_start(side, False, False, 0)
    win.add(outer)

    def mmss(us):
        s = max(0, int(us / 1e6))
        return f"{s // 60}:{s % 60:02d}"

    def load_art(url):
        def work():
            try:
                data = fetch_art(url)
                if data:
                    loader = GdkPixbuf.PixbufLoader()
                    loader.write(data)
                    loader.close()
                    pix = loader.get_pixbuf()
                    GLib.idle_add(lambda: (state.update(art=pix), art_area.queue_draw(), False)[2])
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def show(d):
        clock.set_text(d["time"])
        date.set_text(d["date"])
        p = d["player"]
        progress.set_visible(bool(p and p["length"]))
        times.set_visible(bool(p and p["length"]))
        if p:
            title.set_text(p["title"])
            artist.set_text(p["artist"])
            album.set_text(p["album"])
            player_name.set_text(f'{p["name"]}  ·  {p["status"].lower()}')
            player_icon.set_from_gicon(icon_for([p["entry"]] if p["entry"] else [], "multimedia-player-symbolic"), Gtk.IconSize.LARGE_TOOLBAR)
            elapsed.set_text(mmss(p["position"]))
            total.set_text(mmss(p["length"]) if p["length"] else "")
            progress.set_value(min(1.0, p["position"] / p["length"]) if p["length"] else 0)
            if p["art"] != state["art_url"]:
                state.update(art_url=p["art"], art=None)
                art_area.queue_draw()
                if p["art"]:
                    load_art(p["art"])
        else:
            for w, t in ((title, "Nothing playing"), (artist, ""), (album, ""), (player_name, ""), (elapsed, ""), (total, "")):
                w.set_text(t)
            progress.set_value(0)
            state.update(art=None, art_url=None)
        f = d["front"]
        rows["front"][1].set_text(f["title"] if f else "nothing in front")
        if f:
            rows["front"][0].set_from_gicon(icon_for(f["ids"], "application-x-executable-symbolic"), Gtk.IconSize.DND)
        rows["screen"][1].set_text(d["screen"] or "")
        rows["uptime"][1].set_text(d["uptime"] or "")
        for key, frac, text in (("cpu", None if d["cpu"] is None else d["cpu"] / 100, f'CPU {d["cpu"]}%'),
                                ("mem", d["mem"], d["mem_text"] and f'Memory {d["mem_text"]}'),
                                ("disk", d["disk"], d["disk_text"] and f'Disk {d["disk_text"]}')):
            rows[key][1].set_text(text if frac is not None and text else "")
            rows[key][2].set_value(frac or 0)
        rows["bat"][1].set_text(", ".join(d["batteries"]) or "no pad connected")

    def poll():
        def work():
            data = snapshot(screen_text)
            GLib.idle_add(lambda: (state.update(data=data), show(data), False)[2])
        threading.Thread(target=work, daemon=True).start()
        return True

    poll()
    win.set_size_request(geo.width, HEIGHT)
    win.show_all()
    for key in ("cpu", "mem", "disk"):
        rows[key][2].show()
    win.resize(geo.width, HEIGHT)
    win.move(geo.x, geo.y + geo.height - HEIGHT)                  # the full width, flush with the bottom
    GLib.timeout_add(1000, poll)
    GLib.timeout_add(40, lambda: (bars.queue_draw(), True)[1])

    snap = os.environ.get("CEC_INFO_SNAPSHOT")                      # diagnostics: save a picture of the window
    if snap:
        def take():
            gw = win.get_window()
            pix = Gdk.pixbuf_get_from_window(gw, 0, 0, gw.get_width(), gw.get_height())
            pix.savev(snap, "png", [], [])
            Gtk.main_quit()
            return False
        GLib.timeout_add(3500, take)
    GLib.timeout_add_seconds(int(os.environ.get("CEC_INFO_SECONDS", SHOW_SECONDS)), Gtk.main_quit)
    try:
        Gtk.main()
    finally:
        spectrum.stop()


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
