#!/usr/bin/env python3
"""On-screen keyboard driven by a TV remote (part of CEC-Over-LAN).

The receiver starts this program with the action "osk:toggle". While it is open the receiver
sends it the remote's D-pad over a local socket instead of injecting arrow keys:

    up / down / left / right   move the highlighted key
    ok                         type the highlighted key
    back                       close the keyboard
    close                      close the keyboard (same toggle button pressed again)

The window never takes the keyboard focus (it is shown by Xwayland as a dock-type window with
"input = false"), so the text goes to the application that was in front. Characters are typed
with a virtual keyboard of its own (uinput), so the layout of the machine matters: the tables
below cover Italian ("it") and US ("us"); the layout is read from GNOME, or CEC_OSK_LAYOUT.

The model (OskModel) and the typing tables have no GTK dependency and are unit-tested;
GTK is imported only when the window is built.
"""
import os
import re
import socket
import subprocess
import sys
import time

SOCKET_NAME = "\0cec-over-lan-osk-%d" % os.getuid()  # abstract socket: one instance, no file

# ---------------------------------------------------------------- layout model (no GTK)
# A key is (label, kind, value, width). kinds: char, shift, layer, space, backspace, enter,
# left, right, close. Widths are relative; the real size follows the screen.


def _chars(text, width=2):
    return [(c, "char", c, width) for c in text]


LOWER = [
    _chars("1234567890") + [("⌫", "backspace", None, 3)],
    _chars("qwertyuiop"),
    _chars("asdfghjkl") + [("⏎", "enter", None, 3)],
    [("⇧", "shift", None, 3)] + _chars("zxcvbnm") + _chars(".,"),
    [("?123", "layer", "symbols", 3), ("◀", "left", None, 2),
     ("space", "space", None, 8), ("▶", "right", None, 2), ("✕", "close", None, 3)],
]
UPPER = [
    LOWER[0],
    _chars("QWERTYUIOP"),
    _chars("ASDFGHJKL") + [LOWER[2][-1]],
    [("⇧", "shift", None, 3)] + _chars("ZXCVBNM") + _chars(".,"),
    LOWER[4],
]
SYMBOLS = [
    LOWER[0],
    _chars("!?@#&*()-_"),
    _chars("+=/:;\"'") + [("←", "left", None, 2), ("→", "right", None, 2),
                          ("⏎", "enter", None, 3)],
    [("⇧", "shift", None, 3)] + _chars(".,!?") + _chars("-_@"),
    [("ABC", "layer", "lower", 3), ("◀", "left", None, 2),
     ("space", "space", None, 8), ("▶", "right", None, 2), ("✕", "close", None, 3)],
]
LAYERS = {"lower": LOWER, "upper": UPPER, "symbols": SYMBOLS}


def centre(row, col):
    """Horizontal centre of a key as a fraction of its row (to move up/down between rows)."""
    total = sum(k[3] for k in row)
    return (sum(k[3] for k in row[:col]) + row[col][3] / 2) / total


class OskModel:
    """Which key is highlighted and what pressing it does. One-shot shift, like phone keyboards."""

    def __init__(self):
        self.layer = "lower"
        self.row = 1
        self.col = 0

    @property
    def rows(self):
        return LAYERS[self.layer]

    @property
    def key(self):
        return self.rows[self.row][self.col]

    def _clamp(self):
        self.row = max(0, min(self.row, len(self.rows) - 1))
        self.col = max(0, min(self.col, len(self.rows[self.row]) - 1))

    def move(self, direction):
        rows = self.rows
        if direction in ("left", "right"):
            n = len(rows[self.row])
            self.col = (self.col + (1 if direction == "right" else -1)) % n
        elif direction in ("up", "down"):
            x = centre(rows[self.row], self.col)
            self.row = (self.row + (1 if direction == "down" else -1)) % len(rows)
            new = rows[self.row]
            self.col = min(range(len(new)), key=lambda c: abs(centre(new, c) - x))

    def press(self):
        """Press the highlighted key. Returns what to do outside the model, or None."""
        label, kind, value, _ = self.key
        if kind == "shift":
            self.layer = "lower" if self.layer == "upper" else "upper"
            self._clamp()
            return None
        if kind == "layer":
            self.layer = value
            self._clamp()
            return None
        if kind == "char":
            action = {"type": "char", "char": value}
            if self.layer == "upper":
                self.layer = "lower"  # one-shot shift
            return action
        return {"type": kind}  # space, backspace, enter, left, right, close


# ---------------------------------------------------------------- typing tables (no GTK)
LETTERS = {c: ("KEY_" + c.upper(), ()) for c in "abcdefghijklmnopqrstuvwxyz"}
LETTERS.update({c.upper(): ("KEY_" + c.upper(), ("KEY_LEFTSHIFT",)) for c in "abcdefghijklmnopqrstuvwxyz"})
DIGITS = {d: ("KEY_" + d, ()) for d in "1234567890"}
SH, ALTGR = ("KEY_LEFTSHIFT",), ("KEY_RIGHTALT",)

TABLES = {
    # US layout
    "us": {**LETTERS, **DIGITS,
           ".": ("KEY_DOT", ()), ",": ("KEY_COMMA", ()), "-": ("KEY_MINUS", ()),
           "_": ("KEY_MINUS", SH), "+": ("KEY_EQUAL", SH), "=": ("KEY_EQUAL", ()),
           "/": ("KEY_SLASH", ()), "?": ("KEY_SLASH", SH), ":": ("KEY_SEMICOLON", SH),
           ";": ("KEY_SEMICOLON", ()), "\"": ("KEY_APOSTROPHE", SH), "'": ("KEY_APOSTROPHE", ()),
           "!": ("KEY_1", SH), "@": ("KEY_2", SH), "#": ("KEY_3", SH), "&": ("KEY_7", SH),
           "*": ("KEY_8", SH), "(": ("KEY_9", SH), ")": ("KEY_0", SH)},
    # Italian layout (written from knowledge of the xkb "it" layout: to be verified by typing)
    "it": {**LETTERS, **DIGITS,
           ".": ("KEY_DOT", ()), ",": ("KEY_COMMA", ()), "-": ("KEY_SLASH", ()),
           "_": ("KEY_SLASH", SH), "+": ("KEY_RIGHTBRACE", ()), "=": ("KEY_0", SH),
           "/": ("KEY_7", SH), "?": ("KEY_MINUS", SH), ":": ("KEY_DOT", SH),
           ";": ("KEY_COMMA", SH), "\"": ("KEY_2", SH), "'": ("KEY_MINUS", ()),
           "!": ("KEY_1", SH), "@": ("KEY_SEMICOLON", ALTGR), "#": ("KEY_APOSTROPHE", ALTGR),
           "&": ("KEY_6", SH), "*": ("KEY_RIGHTBRACE", SH), "(": ("KEY_8", SH), ")": ("KEY_9", SH)},
}
SPECIAL = {"space": "KEY_SPACE", "backspace": "KEY_BACKSPACE", "enter": "KEY_ENTER",
           "left": "KEY_LEFT", "right": "KEY_RIGHT"}


def detect_layout():
    """'it' or 'us' (anything unknown is treated as 'us')."""
    forced = os.environ.get("CEC_OSK_LAYOUT")
    if forced in TABLES:
        return forced
    for schema_key in ("mru-sources", "sources"):
        try:
            out = subprocess.run(["gsettings", "get", "org.gnome.desktop.input-sources", schema_key],
                                 capture_output=True, text=True, timeout=3).stdout
        except (subprocess.TimeoutExpired, OSError):
            continue
        m = re.search(r"\('xkb',\s*'([a-z]+)", out)
        if m:
            return m.group(1) if m.group(1) in TABLES else "us"
    return "us"


GAP = 8                    # gap between keys, in pixels
KEY_UNIT_SHARE = 0.66 / 22  # a key of weight 1 is this share of the screen width: the key size
                            # the owner found right (the panel is wider, the keys are not)


def key_unit(screen_width):
    """Pixels of one unit of key width (a normal key is 2 units, the space bar 8)."""
    return max(34, int(screen_width * KEY_UNIT_SHARE))


def all_layout_chars():
    return {k[2] for rows in LAYERS.values() for row in rows for k in row if k[1] == "char"}


class Typist:
    """Types characters through a virtual keyboard of its own."""

    def __init__(self, layout):
        self.table = TABLES[layout]
        self.ui = None

    def open(self):
        from evdev import UInput, ecodes as e
        names = {n for ks, mods in self.table.values() for n in (ks, *mods)} | set(SPECIAL.values())
        self.e = e
        self.ui = UInput({e.EV_KEY: sorted(getattr(e, n) for n in names)},
                         name="TV Remote OSK (CEC-Over-LAN)")

    def _tap(self, key, mods=()):
        e, ui = self.e, self.ui
        for m in mods:
            ui.write(e.EV_KEY, getattr(e, m), 1)
        ui.write(e.EV_KEY, getattr(e, key), 1)
        ui.syn()
        time.sleep(0.01)
        ui.write(e.EV_KEY, getattr(e, key), 0)
        for m in reversed(mods):
            ui.write(e.EV_KEY, getattr(e, m), 0)
        ui.syn()
        time.sleep(0.01)

    def do(self, action):
        if action["type"] == "char" and action["char"] in self.table:
            key, mods = self.table[action["char"]]
            self._tap(key, mods)
        elif action["type"] in SPECIAL:
            self._tap(SPECIAL[action["type"]])


# ---------------------------------------------------------------- the window (GTK)
CSS = b"""
window, window:backdrop { background: rgba(18,20,24,0.97); }
label.title, label.title:backdrop { color: #b4bac5; font-size: 18px; }
button, button:backdrop { font-size: 28px; min-height: 62px; border-radius: 10px; background: #2a2f38;
         color: #f2f4f8; border: 2px solid #3a404b; padding: 0; text-shadow: none; }
button.mod, button.mod:backdrop { background: #343a45; }
button.sel, button.sel:backdrop { background: #3d7bff; color: #ffffff; border-color: #a9c3ff; }
button label, button:backdrop label { color: #f2f4f8; opacity: 1; text-shadow: none; }
button.sel label, button.sel:backdrop label { color: #ffffff; font-weight: bold; }
"""
# The window never has the focus, so GTK would draw everything with the dimmed ":backdrop" look;
# the rules above give both states the same colours.


def main():
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.bind(SOCKET_NAME)
    except OSError:
        sys.exit("the on-screen keyboard is already open")
    layout = detect_layout()
    typist = Typist(layout)
    typist.open()
    model = OskModel()

    win = Gtk.Window(title="CEC-Over-LAN keyboard")
    win.set_decorated(False)
    win.set_accept_focus(False)
    win.set_focus_on_map(False)
    win.set_keep_above(True)
    win.set_skip_taskbar_hint(True)
    win.set_skip_pager_hint(True)
    win.set_type_hint(Gdk.WindowTypeHint.DOCK)
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    # above the user's own theme (~/.config/gtk-3.0/gtk.css has priority USER = 800), otherwise a
    # custom theme can hide the highlighted key
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                             Gtk.STYLE_PROVIDER_PRIORITY_USER + 100)
    display = Gdk.Display.get_default()
    monitor = display.get_primary_monitor() or display.get_monitor(0)
    geo = monitor.get_geometry()
    unit = key_unit(geo.width)        # key size as in the first version

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=GAP)
    outer.set_halign(Gtk.Align.CENTER)   # the keys stay centred on a panel as wide as the screen
    outer.set_margin_start(16)
    outer.set_margin_end(16)
    outer.set_margin_top(12)
    outer.set_margin_bottom(0)        # flush with the bottom edge of the screen
    title = Gtk.Label(label=f"OK types  ·  arrows move  ·  Back closes  ·  layout: {layout}")
    title.get_style_context().add_class("title")
    outer.pack_start(title, False, False, 0)
    board = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=GAP)
    outer.pack_start(board, False, False, 0)
    win.add(outer)
    buttons = []

    def render():
        for child in board.get_children():
            board.remove(child)
        buttons.clear()
        for row in model.rows:
            box = Gtk.Box(spacing=GAP)
            box.set_halign(Gtk.Align.CENTER)
            line = []
            for label, kind, _value, width in row:
                b = Gtk.Button(label=label)
                b.set_can_focus(False)
                b.set_size_request(unit * width, -1)
                if kind != "char":
                    b.get_style_context().add_class("mod")
                box.pack_start(b, False, False, 0)
                line.append(b)
            buttons.append(line)
            board.pack_start(box, False, False, 0)
        board.show_all()
        highlight()

    def highlight():
        for r, line in enumerate(buttons):
            for c, b in enumerate(line):
                ctx = b.get_style_context()
                (ctx.add_class if (r, c) == (model.row, model.col) else ctx.remove_class)("sel")

    def on_message(_fd, _cond):
        msg = sock.recv(64).decode("ascii", "ignore").strip()
        if msg in ("up", "down", "left", "right"):
            model.move(msg)
            highlight()
        elif msg == "ok":
            before = model.layer
            action = model.press()
            if action and action["type"] == "close":
                Gtk.main_quit()
            elif action:
                typist.do(action)
            if model.layer != before:
                render()
            else:
                highlight()
        elif msg in ("back", "close"):
            Gtk.main_quit()
        return True

    render()
    win.show_all()
    size = win.get_preferred_size()[1]
    win.resize(geo.width, size.height)                              # as wide as the screen
    win.move(geo.x, geo.y + geo.height - size.height)               # and flush with the bottom edge
    GLib.io_add_watch(sock.fileno(), GLib.IO_IN, on_message)

    snapshot = os.environ.get("CEC_OSK_SNAPSHOT")  # diagnostics: save a picture of the window
    if snapshot:
        def take_snapshot():
            gdk_win = win.get_window()
            pixbuf = Gdk.pixbuf_get_from_window(gdk_win, 0, 0, gdk_win.get_width(), gdk_win.get_height())
            print(f"[osk-snapshot] visible={gdk_win.is_visible()} viewable={gdk_win.is_viewable()} "
                  f"pos={win.get_position()} size={gdk_win.get_width()}x{gdk_win.get_height()} "
                  f"keys_block={outer.get_allocated_width()}px unit={unit} "
                  f"monitor={geo.width}x{geo.height}+{geo.x}+{geo.y} pixbuf={'ok' if pixbuf else 'None'}",
                  file=sys.stderr, flush=True)
            if pixbuf is not None:
                pixbuf.savev(snapshot, "png", [], [])
            return False
        GLib.timeout_add(int(float(os.environ.get("CEC_OSK_SNAPSHOT_DELAY", "4")) * 1000), take_snapshot)
    Gtk.main()


if __name__ == "__main__":
    main()
