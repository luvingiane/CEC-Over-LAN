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
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

SHOW_SECONDS = 8
ENTER_S, EXIT_S = 0.32, 0.24      # the panel rises from the bottom edge, fading in; it leaves the same way


def animation(t, entering):
    """(slide, alpha) for the progress t (0..1) of the entrance or the exit: slide is the part of the panel's
    height it is still below its place (1 = fully below the edge, 0 = in place), alpha its opacity."""
    t = min(1.0, max(0.0, t))
    if entering:
        p = 1 - (1 - t) ** 3                    # fast at first, easing into place
        return 1 - p, min(1.0, p * 1.6)
    p = t ** 3                                  # slow at first, then away
    return p, max(0.0, 1 - p * 1.3)


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


def battery_entries(root="/sys/class/power_supply"):
    """[(directory name, capacity, status)] of the batteries of devices (pads, mice), not of the machine."""
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
        out.append((d.name, cap, status))
    return out


def battery_text(cap, status):
    return f"{cap}%" + (f" ({status})" if status and status != "discharging" else "")


def batteries(root="/sys/class/power_supply"):
    """['pad 65% (charging)', ...]: every battery of a device, by its own name."""
    out = []
    for name, cap, status in battery_entries(root):
        short = name.replace("ps-controller-battery-", "pad ").replace("hid-", "").split("-battery")[0]
        out.append(f"{short} {battery_text(cap, status)}")
    return out


def pads(proc="/proc/bus/input/devices"):
    """[(name, uniq)] of the game controllers the kernel sees (the devices that have a js handler), without the
    motion sensor and touchpad parts of a pad and without our own virtual devices."""
    import re
    try:
        text = Path(proc).read_text()
    except OSError:
        return []
    found = []
    for block in text.split("\n\n"):
        name = re.search(r'^N: Name="(.*)"', block, re.M)
        handlers = re.search(r"^H: Handlers=(.*)$", block, re.M)
        if not name or not handlers or not re.search(r"\bjs\d+\b", handlers.group(1)):
            continue
        n = name.group(1)
        if any(x in n for x in ("Motion Sensors", "Touchpad", "CEC-Over-LAN")):
            continue
        uniq = re.search(r"^U: Uniq=(.*)$", block, re.M)
        entry = (n, (uniq.group(1).strip().lower() if uniq else ""))
        if entry not in found:
            found.append(entry)
    return found


def devices(proc="/proc/bus/input/devices", root="/sys/class/power_supply"):
    """['DualSense Wireless Controller 65% (charging)', '8BitDo Ultimate 2 Wireless Controller', ...]: the pads that
    are connected (with their battery when the kernel reports one for them) and the batteries of other devices.
    Many pads (2.4 GHz receivers, wired ones) report no battery at all: they are listed by name."""
    left = battery_entries(root)
    out = []
    for name, uniq in pads(proc):
        match = next((b for b in left if uniq and uniq.replace(":", "") in b[0].lower().replace(":", "")), None)
        if match:
            left.remove(match)
            out.append(f"{name} {battery_text(match[1], match[2])}")
        else:
            out.append(name)
    for dirname, cap, status in left:
        short = dirname.replace("ps-controller-battery-", "pad ").replace("hid-", "").split("-battery")[0]
        out.append(f"{short} {battery_text(cap, status)}")
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


# windows that are the desktop or the frontend, not "an app in front": music keeps the panel open over them
FRONTEND_NAMES = ("es-de", "esde", "gnome-shell", "org.gnome.shell", "mutter")


def is_app(front):
    """True when `front` ({'title', 'ids'} of the focused window, or None) is a real application."""
    if not front:
        return False
    text = " ".join([front.get("title", "")] + list(front.get("ids", []))).lower()
    return not any(name in text for name in FRONTEND_NAMES)


def is_sticky(player, front):
    """The panel stays open (until the action is used again) while music plays and no application is in front:
    nothing is in the way of it, and it is what one wants to look at. Anything else: it closes by itself."""
    return bool(player and player.get("status") == "Playing" and not is_app(front))


def snapshot(screen=None):
    """Everything the panel shows, as plain data (the window only draws it)."""
    pls = players()
    front = in_front()
    return {"time": datetime.now().strftime("%H:%M"), "date": datetime.now().strftime("%a %d %b"),
            "player": pls[0] if pls else None, "front": front, "sticky": is_sticky(pls[0] if pls else None, front),
            "screen": screen or screen_mode(),
            "uptime": uptime(), "cpu": cpu_percent(), "mem": memory_fraction(), "mem_text": memory(),
            "disk": disk_fraction(), "disk_text": disk(), "batteries": devices()}


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


# ---------------------------------------------------------------- GNOME colours (no GTK)
ADWAITA_ACCENT = {"blue": "#3584e4", "teal": "#2190a4", "green": "#3a944a", "yellow": "#c88800", "orange": "#ed5b00",
                  "red": "#e62d42", "pink": "#d56199", "purple": "#9141ac", "slate": "#6f8396"}


def parse_color(text):
    """(r, g, b, a) in 0..1 from '#rrggbb', '#rgb', 'rgb(...)' or 'rgba(...)'; None for anything else (mix(), @names...)."""
    import re
    text = (text or "").strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", text)
    if m:
        v = m.group(1)
        return tuple(int(v[i:i + 2], 16) / 255 for i in (0, 2, 4)) + (1.0,)
    m = re.fullmatch(r"#([0-9a-fA-F]{3})", text)
    if m:
        return tuple(int(c * 2, 16) / 255 for c in m.group(1)) + (1.0,)
    m = re.fullmatch(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)", text)
    if m:
        r, g, b = (min(255.0, float(m.group(i))) / 255 for i in (1, 2, 3))
        return (r, g, b, float(m.group(4)) if m.group(4) else 1.0)
    return None


def mix(a, b, t):
    """a moved towards b by t (0..1), alpha kept from a."""
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3)) + (a[3],)


def luminance(c):
    def lin(v):
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2])


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def readable_on(fg, bg, ratio):
    """fg pushed towards white (on a dark background) or black (on a light one) until it reads against bg."""
    end = (1.0, 1.0, 1.0, 1.0) if luminance(bg) < 0.4 else (0.0, 0.0, 0.0, 1.0)
    for step in range(11):
        c = mix(fg, end, step / 10)
        if contrast(c, bg) >= ratio:
            return c
    return end


def define_colors(path, _seen=None):
    """{name: value text} of the @define-color lines of a GTK css file and of the files it @imports."""
    import re
    path, _seen = Path(path), _seen if _seen is not None else set()
    out = {}
    if path in _seen or not path.is_file():
        return out
    _seen.add(path)
    try:
        text = path.read_text()
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"@import\s+url\(\"?([^\")]+)\"?\)\s*;", line)
        if m:
            out.update(define_colors(path.parent / m.group(1), _seen))
            continue
        m = re.match(r"@define-color\s+([\w-]+)\s+(.+?)\s*;", line)
        if m:
            out[m.group(1).replace("_", "-")] = m.group(2)
    return out


def palette(css=None, accent_name="blue", scheme="prefer-dark"):
    """The panel's colours, from the user's GTK theme (named colours such as window-bg-color, which a theme
    manager like Rewaita writes) and, where it names none, from GNOME's accent colour and dark/light setting."""
    names = define_colors(css if css is not None else Path.home() / ".config/gtk-3.0/gtk.css")
    dark = scheme != "prefer-light"

    def pick(*keys, default):
        for k in keys:
            c = parse_color(names.get(k))
            if c:
                return c
        return parse_color(default)
    bg = pick("window-bg-color", default="#1e1e1e" if dark else "#fafafa")
    fg = pick("window-fg-color", default="#ffffff" if dark else "#241f31")
    accent = pick("accent-bg-color", "accent-color", default=ADWAITA_ACCENT.get(accent_name, "#3584e4"))
    return {"bg": bg, "fg": fg, "accent": accent,
            "card": pick("card-bg-color", default="#303030" if dark else "#ffffff"),
            # text that stays readable over the bars and the artwork, as bright as the theme allows
            "text": readable_on(fg, bg, 12.0), "soft": readable_on(fg, bg, 8.0),
            # the spectrum is the quiet layer: the accent, darker than the text and tinted by the background
            "bars": mix(accent, bg, 0.5)}


def gnome_settings():
    """(accent colour name, colour scheme) from GSettings, with the defaults when it cannot be read."""
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
        s = Gio.Settings.new("org.gnome.desktop.interface")

        def get(key, default):
            try:
                return s.get_string(key)
            except Exception:                  # older GNOME: no accent-color key
                return default
        return get("accent-color", "blue"), get("color-scheme", "prefer-dark")
    except Exception:
        return "blue", "prefer-dark"


# ---------------------------------------------------------------- spectrum and artwork (no GTK)
BANDS = 64


def band_edges(rate=44100, n=1024, bands=BANDS, lo=40.0, hi=16000.0):
    """FFT bin boundaries of `bands` log-spaced bands between lo and hi Hz."""
    import numpy as np
    freqs = np.geomspace(lo, hi, bands + 1)
    bins = np.unique(np.clip((freqs * n / rate).astype(int), 1, n // 2))
    return bins


def levels_from_samples(samples, edges, previous=None, fall=0.9):
    """0..1 level per band from int16 samples; `previous` makes the bars fall smoothly (`fall` per call)."""
    import numpy as np
    x = samples.astype(np.float32) / 32768.0
    mag = np.abs(np.fft.rfft(x * np.hanning(len(x)))) / len(x)
    out = []
    for i in range(len(edges) - 1):
        m = float(mag[edges[i]:max(edges[i] + 1, edges[i + 1])].max())
        db = 20 * np.log10(m + 1e-9)
        out.append(min(1.0, max(0.0, (db + 78.0) / 60.0)))
    if previous:
        out = [max(v, p * fall) for v, p in zip(out, previous)]
    return out


class Spectrum(threading.Thread):
    """What the machine is playing, as band levels: pw-record on the monitor of the output, numpy FFT."""
    N, HOP = 2048, 512             # a 46 ms window moved every 11 ms: about 86 updates a second

    def __init__(self):
        super().__init__(daemon=True)
        self.levels = [0.0] * BANDS
        self.proc = None
        self.stopped = False

    def run(self):
        try:
            import numpy as np
            edges = band_edges(n=self.N)
            self.levels = [0.0] * (len(edges) - 1)
            self.proc = subprocess.Popen(
                ["pw-record", "-a", "-P", "stream.capture.sink=true", "--rate", "44100", "--channels", "1",
                 "--format", "s16", "--latency", "10ms", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            window = np.zeros(self.N, dtype="<i2")
            while not self.stopped:
                raw = self.proc.stdout.read(self.HOP * 2)
                if len(raw) < self.HOP * 2:
                    break
                window = np.concatenate([window[self.HOP:], np.frombuffer(raw, dtype="<i2")])
                self.levels = levels_from_samples(window, edges, self.levels)
        except Exception:
            pass                       # no numpy or no pw-record: the bars stay flat

    def stop(self):
        self.stopped = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


ART_MAX_BYTES = 3_000_000


def letterbox(row_means, threshold=14):
    """(top, bottom) rows of black bars to cut from a picture, given the mean brightness of every row
    (YouTube's 4:3 thumbnails carry bars above and below a 16:9 picture). At most a third each way."""
    n = len(row_means)
    top = 0
    while top < n // 3 and row_means[top] < threshold:
        top += 1
    bottom = 0
    while bottom < n // 3 and row_means[n - 1 - bottom] < threshold:
        bottom += 1
    return top, bottom


def crop_bars(pix):
    """The picture without the black bars above and below it, or as it is. GdkPixbuf's pixel buffer
    ends with a short last row, so it is padded before it is reshaped."""
    try:
        import numpy as np
        w, h, rs, n = pix.get_width(), pix.get_height(), pix.get_rowstride(), pix.get_n_channels()
        buf = bytes(pix.get_pixels())
        rows = np.frombuffer(buf + b"\0" * (h * rs - len(buf)), dtype=np.uint8).reshape(h, rs)[:, :w * n].reshape(h, w, n)[:, :, :3]
        top, bottom = letterbox(list(rows.mean(axis=(1, 2))))
        if top + bottom and h - top - bottom >= h // 2:
            return pix.new_subpixbuf(0, top, w, h - top - bottom)
    except Exception:
        pass
    return pix


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
HEIGHT = 430          # logical pixels: the right column needs about this much
PROGRESS_H = 8
ART_SHARE = 0.40                 # the artwork covers this part of the panel's width, from the left edge
TEXT_MARGIN = 72                 # the media text starts at the left edge, over the artwork: long titles need the room


def css_rgba(c, alpha=None):
    return "rgba(%d,%d,%d,%.3f)" % (round(c[0] * 255), round(c[1] * 255), round(c[2] * 255), c[3] if alpha is None else alpha)


def build_css(pal):
    text, soft, bg, ac = pal["text"], pal["soft"], pal["bg"], pal["accent"]
    # no blur here: a blurred shadow is redone for every label at every frame and cut the panel from 60 to 20 fps
    shadow = f"text-shadow: 1px 2px 0 {css_rgba(bg, 0.85)};"
    return f"""
label, label:backdrop {{ color: {css_rgba(soft)}; font-size: 22px; {shadow} }}
label.title, label.title:backdrop {{ font-size: 42px; font-weight: bold; color: {css_rgba(text)}; }}
label.artist, label.artist:backdrop {{ font-size: 28px; color: {css_rgba(text)}; }}
label.muted, label.muted:backdrop {{ font-size: 20px; color: {css_rgba(soft)}; }}
label.clock, label.clock:backdrop {{ font-size: 52px; font-weight: bold; color: {css_rgba(text)}; }}
levelbar trough, levelbar trough:backdrop {{ background: {css_rgba(soft, 0.25)}; min-height: 10px; border-radius: 5px; }}
levelbar block.filled, levelbar block.filled:backdrop {{ background: {css_rgba(ac)}; border-radius: 5px; }}
image, image:backdrop {{ color: {css_rgba(soft)}; }}
""".encode()


def rounded_rect(cr, x, y, w, h, r):
    import math
    r = max(0.0, min(r, w / 2, h / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def main():
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    gi.require_version("Pango", "1.0")
    from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango
    import cairo

    accent_name, scheme = gnome_settings()
    pal = palette(accent_name=accent_name, scheme=scheme)
    win = Gtk.Window(title="CEC-Over-LAN info")
    win.set_decorated(False)
    win.set_accept_focus(False)
    win.set_focus_on_map(False)
    win.set_keep_above(True)
    win.set_skip_taskbar_hint(True)
    win.set_type_hint(Gdk.WindowTypeHint.DOCK)
    win.set_app_paintable(True)
    provider = Gtk.CssProvider()
    provider.load_from_data(build_css(pal))
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 100)
    display = Gdk.Display.get_default()
    monitor = display.get_primary_monitor() or display.get_monitor(0)
    geo = monitor.get_geometry()
    screen_text = screen_mode() or f"{geo.width}x{geo.height} @ {monitor.get_refresh_rate() / 1000:.0f} Hz"

    spectrum = Spectrum()
    spectrum.start()
    state = {"data": None, "art": None, "art_url": None, "t0": time.monotonic(), "back": None, "back_key": None}
    shown = []                                   # the bars as drawn: they follow the spectrum with some inertia

    def label(text="", css=None, xalign=0.0):
        w = Gtk.Label(label=text, xalign=xalign)
        if css:
            w.get_style_context().add_class(css)
        w.set_ellipsize(Pango.EllipsizeMode.END)
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

    # ---- the canvas behind everything: the artwork faded out to the right, the spectrum, the progress bar
    canvas = Gtk.DrawingArea()
    canvas.set_hexpand(True)
    canvas.set_vexpand(True)

    def backdrop(w, h):
        """The artwork over the left ART_SHARE of the panel, dimmed and fading to nothing at its end (cached)."""
        pix = state["art"]
        if pix is None:
            return None
        key = (id(pix), w, h)
        if state["back_key"] != key:
            aw = int(w * ART_SHARE)
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, aw, h)
            cr = cairo.Context(surf)
            s = max(aw / pix.get_width(), h / pix.get_height())
            sw, sh = int(pix.get_width() * s + 0.5), int(pix.get_height() * s + 0.5)
            scaled = pix.scale_simple(sw, sh, GdkPixbuf.InterpType.BILINEAR)
            Gdk.cairo_set_source_pixbuf(cr, scaled, (aw - sw) / 2, (h - sh) / 2)
            fade = cairo.LinearGradient(0, 0, aw, 0)
            fade.add_color_stop_rgba(0.0, 0, 0, 0, 0.50)
            fade.add_color_stop_rgba(0.45, 0, 0, 0, 0.28)
            fade.add_color_stop_rgba(1.0, 0, 0, 0, 0.0)
            cr.mask(fade)
            state["back"], state["back_key"] = surf, key
        return state["back"]

    def fraction():
        d = state["data"]
        p = d and d["player"]
        if not p or not p["length"]:
            return None
        pos = p["position"] + ((time.monotonic() - state["t0"]) * 1e6 if p["status"] == "Playing" else 0)
        return max(0.0, min(1.0, pos / p["length"]))

    stats = {"frames": 0, "draw": 0.0, "last": time.monotonic(), "draws": 0}

    def draw_canvas(widget, cr):
        t_start = time.perf_counter()
        w, h = widget.get_allocated_width(), widget.get_allocated_height()
        bg = pal["bg"]
        cr.set_source_rgba(bg[0], bg[1], bg[2], 0.97)
        cr.paint()
        back = backdrop(w, h)
        if back is not None:
            cr.set_source_surface(back, 0, 0)
            cr.paint()
        ac, bars = pal["accent"], pal["bars"]
        n = len(shown)
        if n:
            gap = max(3.0, w / n * 0.18)
            bw = (w - gap * (n - 1)) / n
            for i, v in enumerate(shown):
                bh = 4 + v * (h - PROGRESS_H - 30) * 0.6        # behind the text: low enough to leave it readable
                rounded_rect(cr, i * (bw + gap), h - PROGRESS_H - 6 - bh, bw, bh, min(bw * 0.4, 7))
                cr.set_source_rgba(bars[0], bars[1], bars[2], 0.30 + 0.35 * v)
                cr.fill()
        frac = fraction()
        if frac is not None:
            fg = pal["fg"]
            cr.set_source_rgba(fg[0], fg[1], fg[2], 0.18)
            cr.rectangle(0, h - PROGRESS_H, w, PROGRESS_H)
            cr.fill()
            cr.set_source_rgba(ac[0], ac[1], ac[2], 1.0)
            cr.rectangle(0, h - PROGRESS_H, w * frac, PROGRESS_H)
            cr.fill()
        stats["draw"] += time.perf_counter() - t_start
        stats["draws"] += 1
        return False
    canvas.connect("draw", draw_canvas)

    # ---- text
    player_icon = Gtk.Image()
    player_name = label("", "muted")
    title = label("Nothing playing", "title")
    title.set_line_wrap(True)
    title.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    title.set_lines(3)
    title.set_max_width_chars(70)
    artist, album = label("", "artist"), label("", "muted")
    elapsed, remaining = label("", "muted"), label("", "muted", 1.0)
    head = Gtk.Box(spacing=10)
    head.pack_start(player_icon, False, False, 0)
    head.pack_start(player_name, True, True, 0)
    mid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    for w in (head, title, artist, album):
        mid.pack_start(w, False, False, 0)
    mid.set_hexpand(True)
    mid.set_valign(Gtk.Align.START)               # towards the top

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

    side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    side.set_size_request(520, -1)
    side.pack_start(clock, False, False, 0)
    side.pack_start(date, False, False, 0)
    for key, icon_name in (("front", "application-x-executable-symbolic"), ("screen", "video-display-symbolic"),
                           ("uptime", "preferences-system-time-symbolic"), ("cpu", "utilities-system-monitor-symbolic"),
                           ("mem", "media-flash-symbolic"), ("disk", "drive-harddisk-symbolic"),
                           ("bat", "battery-good-symbolic")):
        side.pack_start(row(key, icon_name), False, False, 0)

    outer = Gtk.Box(spacing=36)
    outer.set_margin_start(TEXT_MARGIN)
    outer.set_margin_end(56)
    outer.set_margin_top(40)
    outer.set_margin_bottom(24 + PROGRESS_H)
    outer.pack_start(mid, True, True, 0)
    outer.pack_start(side, False, False, 0)
    times = Gtk.Box()                                  # the ends of the panel, just above the progress bar
    times.set_valign(Gtk.Align.END)
    times.set_margin_start(22)
    times.set_margin_end(22)
    times.set_margin_bottom(PROGRESS_H + 6)
    times.pack_start(elapsed, False, False, 0)
    times.pack_end(remaining, False, False, 0)
    overlay = Gtk.Overlay()
    overlay.add(canvas)
    overlay.add_overlay(outer)
    overlay.add_overlay(times)
    win.add(overlay)

    def mmss(us):
        s = max(0, int(us / 1e6))
        return f"{s // 60}:{s % 60:02d}"

    def set_times(p, pos):
        """Played so far on the left, what is left (not the whole length) on the right."""
        elapsed.set_text(mmss(pos))
        remaining.set_text("-" + mmss(p["length"] - pos) if p["length"] else "")

    def load_art(url):
        def work():
            try:
                data = fetch_art(url)
                if data:
                    loader = GdkPixbuf.PixbufLoader()
                    loader.write(data)
                    loader.close()
                    pix = crop_bars(loader.get_pixbuf())
                    GLib.idle_add(lambda: (state.update(art=pix), canvas.queue_draw(), False)[2])
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def show(d):
        clock.set_text(d["time"])
        date.set_text(d["date"])
        p = d["player"]
        state["t0"] = time.monotonic()
        if p:
            title.set_text(p["title"])
            artist.set_text(p["artist"])
            album.set_text(p["album"])
            player_name.set_text(f'{p["name"]}  ·  {p["status"].lower()}')
            player_icon.set_from_gicon(icon_for([p["entry"]] if p["entry"] else [], "multimedia-player-symbolic"), Gtk.IconSize.LARGE_TOOLBAR)
            set_times(p, p["position"])
            if p["art"] != state["art_url"]:
                state.update(art_url=p["art"], art=None)
                canvas.queue_draw()
                if p["art"]:
                    load_art(p["art"])
        else:
            for w, t in ((title, "Nothing playing"), (artist, ""), (album, ""), (player_name, ""), (elapsed, ""), (remaining, "")):
                w.set_text(t)
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
        rows["bat"][1].set_text(", ".join(d["batteries"]) or "no controller connected")

    def poll():
        def work():
            data = snapshot(screen_text)
            GLib.idle_add(lambda: (state.update(data=data), show(data), False)[2])
        threading.Thread(target=work, daemon=True).start()
        return True

    def frame():
        """60 times a second: the bars move towards the spectrum (up fast, down slowly), the bar of the song advances."""
        target = spectrum.levels
        if len(shown) != len(target):
            shown[:] = [0.0] * len(target)
        for i, v in enumerate(target):
            shown[i] += (v - shown[i]) * (0.55 if v > shown[i] else 0.14)
        d = state["data"]
        p = d and d["player"]
        if p and p["length"] and p["status"] == "Playing":
            pos = p["position"] + (time.monotonic() - state["t0"]) * 1e6
            set_times(p, min(pos, p["length"]))
        h = canvas.get_allocated_height()
        top = int(h * 0.28)                      # the bars never rise above this: the rest of the panel stays as painted
        canvas.queue_draw_area(0, top, canvas.get_allocated_width(), h - top)
        if os.environ.get("CEC_INFO_TRACE"):
            stats["frames"] += 1
            now = time.monotonic()
            if now - stats["last"] >= 1.0:
                print(f"fps: ticks={stats['frames']} draws={stats['draws']} draw_ms={1000 * stats['draw'] / max(1, stats['draws']):.1f}", flush=True)
                stats.update(frames=0, draw=0.0, draws=0, last=now)
        return True

    poll()
    win.set_size_request(geo.width, -1)
    win.set_opacity(0.0)                   # invisible until it is in place below the edge; then it rises
    win.show_all()
    for key in ("cpu", "mem", "disk"):
        rows[key][2].show()
    # a GtkOverlay sizes itself from its main child only: the content decides the height here
    height = min(max(HEIGHT, outer.get_preferred_height()[1]), geo.height // 2)
    canvas.set_size_request(-1, height)
    win.resize(geo.width, height)
    base_y = geo.y + geo.height - height                          # the full width, flush with the bottom
    trace = os.environ.get("CEC_INFO_TRACE")
    run = {"t0": time.monotonic(), "entering": True, "closing": False}

    def place(slide, alpha):
        win.set_opacity(alpha)
        win.move(geo.x, int(base_y + height * slide))
        if trace:
            print(f"anim {'in' if run['entering'] else 'out'} slide={slide:.2f} alpha={alpha:.2f} y={win.get_position()[1]}", flush=True)

    def step():
        dur = ENTER_S if run["entering"] else EXIT_S
        t = (time.monotonic() - run["t0"]) / dur
        place(*animation(t, run["entering"]))
        if t < 1.0:
            return True
        if not run["entering"]:
            Gtk.main_quit()
        else:
            GLib.timeout_add_seconds(int(os.environ.get("CEC_INFO_SECONDS", SHOW_SECONDS)), deadline)   # shown for a while, then away
        return False

    def deadline():
        """The time is up: it closes, unless music plays with no application in front; then it stays and
        looks again every second, and closes when that stops being true."""
        d = state["data"]
        if d and d["sticky"]:
            GLib.timeout_add_seconds(1, recheck)
        else:
            close()
        return False

    def recheck():
        d = state["data"]
        if d and d["sticky"] and not run["closing"]:
            return True
        close()
        return False

    def close(*_):
        """Leave the same way it came (also what SIGTERM does: the receiver closes the panel by terminating it)."""
        if not run["closing"]:
            run.update(closing=True, entering=False, t0=time.monotonic())
            GLib.timeout_add(16, step)
        return False

    place(*animation(0.0, True))
    GLib.timeout_add(16, step)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, close)
    GLib.timeout_add(1000, poll)
    canvas.add_tick_callback(lambda widget, clock: frame())      # in step with the screen, not a blind 16 ms timer

    snap = os.environ.get("CEC_INFO_SNAPSHOT")                      # diagnostics: save a picture of the window
    if snap:
        def take():
            gw = win.get_window()
            pix = Gdk.pixbuf_get_from_window(gw, 0, 0, gw.get_width(), gw.get_height())
            pix.savev(snap, "png", [], [])
            Gtk.main_quit()
            return False
        GLib.timeout_add(3500, take)
    try:
        Gtk.main()
    finally:
        spectrum.stop()


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
