#!/usr/bin/env python3
"""CEC-Over-LAN receiver for a Linux VM/PC.

Reads the key presses of a Google TV remote through
`adb shell getevent -l` ("variant A", no app on the TV) and re-injects them
into a uinput device, but ONLY while the TV is actually showing this machine
(the active CEC routing path equals the HDMI port the machine is plugged into).

The key mapping lives in a JSON file (default ~/cec-over-lan/keymap.json) that
is reloaded automatically when it changes; the web UI edits it. Without that
file the built-in default mapping is used.

Usage:
  tv_remote.py --target <IP:port> --dry-run     # print only, inject/launch nothing
  tv_remote.py --target <IP:port>               # inject, only while the TV shows this machine
  tv_remote.py --target <IP:port> --no-gate     # always inject (no source check)

keymap.json format:
  {"version": 1, "keys": {
      "KEY_SELECT":  {"tap": "KEY_ENTER"},
      "KEY_EJECTCD": {"long": "KEY_LEFTALT+KEY_F4", "hold": 3.0},
      "KEY_8":       {"tap": "KEY_8", "long": "KEY_LEFTCTRL+KEY_8", "hold": 1.0},
      "KEY_FIND":    {"tap": "app:kodi.desktop", "long": "url:https://example.org/"}}}
  An action is one of:
    "KEY_A" or "KEY_LEFTCTRL+KEY_C"  keys, pressed on DOWN and released on UP
                                       (clicked on release when a "long" action also exists)
    "app:<file>.desktop"              launch an installed application (gio launch)
    "url:http(s)://..."               open a web page with the default browser (xdg-open)
  "long" fires once after "hold" seconds (default 3). No free-form commands are accepted.
"""
import argparse
import ast
import difflib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

LINE = re.compile(r"/dev/input/event\d+:\s+EV_KEY\s+(KEY_\w+)\s+(DOWN|UP)\b")
PATH = re.compile(r"mActiveRoutingPath:\s*(0x[0-9a-fA-F]+)")
KEY_NAME = re.compile(r"^KEY_[A-Z0-9_]+$")
DESKTOP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,200}\.desktop$")
URL_RE = re.compile(r"^https?://[^\s\x00-\x1f\x7f]{1,2000}$")
STATE_DIR = Path(os.environ.get("CEC_STATE_DIR", Path.home() / "cec-over-lan"))
KEYMAP_FILE = STATE_DIR / "keymap.json"
DRYRUN_FILE = STATE_DIR / "dry-run"
POINTER_FILE = STATE_DIR / "pointer-visible"
APP_DIRS = [Path("/usr/share/applications"), Path("/usr/local/share/applications"),
            Path.home() / ".local/share/applications",
            Path("/var/lib/flatpak/exports/share/applications"),
            Path.home() / ".local/share/flatpak/exports/share/applications"]
DEFAULT_HOLD = 3.0
LAUNCH_DEBOUNCE_S = 1.5
# never injectable: they would suspend/power off the machine
DENY_TARGETS = {"KEY_POWER", "KEY_SLEEP", "KEY_SUSPEND", "KEY_RESTART", "KEY_WAKEUP",
                "KEY_POWER2", "KEY_COFFEE"}

try:
    from evdev import UInput, ecodes as e
except ImportError:  # lets --dry-run and the web UI work without evdev
    UInput = None
    e = None


def valid_key(name):
    return isinstance(name, str) and bool(KEY_NAME.match(name)) and (e is None or hasattr(e, name))


def valid_target(name):
    return valid_key(name) and name not in DENY_TARGETS


# Names people actually type for modifiers and common keys -> the evdev key name.
ALIASES = {
    "META": "KEY_LEFTMETA", "SUPER": "KEY_LEFTMETA", "WIN": "KEY_LEFTMETA", "WINDOWS": "KEY_LEFTMETA",
    "GUI": "KEY_LEFTMETA", "CMD": "KEY_LEFTMETA", "COMMAND": "KEY_LEFTMETA",
    "CTRL": "KEY_LEFTCTRL", "CONTROL": "KEY_LEFTCTRL", "ALT": "KEY_LEFTALT", "ALTGR": "KEY_RIGHTALT",
    "SHIFT": "KEY_LEFTSHIFT", "ESCAPE": "KEY_ESC", "RETURN": "KEY_ENTER", "DEL": "KEY_DELETE",
    "INS": "KEY_INSERT", "PGUP": "KEY_PAGEUP", "PGDN": "KEY_PAGEDOWN", "PAGE_UP": "KEY_PAGEUP",
    "PAGE_DOWN": "KEY_PAGEDOWN", "SPACEBAR": "KEY_SPACE",
}


def canon_key(token):
    """'Super', 'KEY_META', 'ctrl', 'f4', 'KEY_f4' -> 'KEY_LEFTMETA', 'KEY_LEFTMETA', 'KEY_LEFTCTRL',
    'KEY_F4', 'KEY_F4'. Unknown names are returned upper-cased (and rejected later)."""
    t = token.strip().upper().replace(" ", "_")
    bare = t[4:] if t.startswith("KEY_") else t
    if bare in ALIASES:
        return ALIASES[bare]
    return t if t.startswith("KEY_") else "KEY_" + t


def parse_combo(text):
    """'KEY_LEFTALT+KEY_F4', 'Alt+F4', 'Super+S' -> evdev names, or None if invalid/empty."""
    if not isinstance(text, str):
        return None
    names = [canon_key(p) for p in text.split("+") if p.strip()]
    if not names or len(names) > 4 or not all(valid_target(n) for n in names):
        return None
    return names


def combo_hint(text):
    """Why a key combination was refused, in words the owner can act on."""
    if not isinstance(text, str) or not text.strip():
        return "empty"
    parts = [p for p in text.split("+") if p.strip()]
    if len(parts) > 4:
        return "at most 4 keys per combination"
    for p in parts:
        name = canon_key(p)
        if name in DENY_TARGETS:
            return f"{name} is not allowed (it would power off or suspend the machine)"
        if not valid_key(name):
            stem = name[4:]
            names = []
            if e is not None:
                names = sorted({n for v in e.keys.values() for n in (v if isinstance(v, (list, tuple)) else [v])
                                if n.startswith("KEY_")})
            close = [n for n in names if stem and stem in n][:3] or difflib.get_close_matches(name, names, n=3)
            return f"unknown key '{p.strip()}'" + (f" (did you mean {', '.join(close)}?)" if close else "")
    return "invalid"


MOUSE_BUTTONS = {"left": "BTN_LEFT", "right": "BTN_RIGHT", "middle": "BTN_MIDDLE"}
MOUSE_MOVES = {"move-up": (0, -1), "move-down": (0, 1), "move-left": (-1, 0), "move-right": (1, 0)}
MOUSE_SCROLL = {"scroll-up": 1, "scroll-down": -1}
MOUSE_NAMES = sorted([*MOUSE_BUTTONS, *MOUSE_MOVES, *MOUSE_SCROLL])


def parse_action(text):
    """Text -> {"type": "keys"|"app"|"url"|"osk"|"mouse", ...} or None when invalid."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if text == "osk:toggle":
        return {"type": "osk"}
    if text.startswith("mouse:"):
        name = text[6:].strip()
        return {"type": "mouse", "name": name} if name in MOUSE_NAMES else None
    if text.startswith("app:"):
        app = text[4:].strip()
        return {"type": "app", "id": app} if DESKTOP_ID.match(app) else None
    if text.startswith("url:"):
        url = text[4:].strip()
        return {"type": "url", "url": url} if URL_RE.match(url) else None
    keys = parse_combo(text)
    return {"type": "keys", "keys": keys} if keys else None


def action_hint(text):
    """Why an action text was refused (keys, app:, url:)."""
    t = str(text).strip()
    if t.startswith("app:"):
        return "an application is written app:<file>.desktop"
    if t.startswith("url:"):
        return "a website is written url:http(s)://…"
    if t.startswith("osk:"):
        return "the only keyboard action is osk:toggle"
    if t.startswith("mouse:"):
        return "a mouse action is mouse:<" + "|".join(MOUSE_NAMES) + ">"
    return combo_hint(t)


def action_to_text(action):
    if action["type"] == "osk":
        return "osk:toggle"
    if action["type"] == "mouse":
        return "mouse:" + action["name"]
    if action["type"] == "keys":
        return "+".join(action["keys"])
    if action["type"] == "app":
        return "app:" + action["id"]
    return "url:" + action["url"]


def brief(action):
    """Short, space-free description of an action for the log (URLs are reduced to the host)."""
    if action["type"] == "url":
        return "url:" + urlparse(action["url"]).netloc
    return action_to_text(action)


def desktop_path(app_id):
    """Path of an installed .desktop file, or None."""
    if not DESKTOP_ID.match(app_id or ""):
        return None
    for d in APP_DIRS:
        p = d / app_id
        if p.is_file():
            return p
    return None


_SESSION_ENV = {}


def session_env():
    """Environment for launched GUI programs: the graphical session variables of systemd --user."""
    if not _SESSION_ENV:
        env = dict(os.environ)
        try:
            out = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True,
                                 text=True, timeout=5).stdout
            for line in out.splitlines():
                k, _, v = line.partition("=")
                if k in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
                         "XDG_CURRENT_DESKTOP", "XAUTHORITY"):
                    env.setdefault(k, v)
        except (subprocess.TimeoutExpired, OSError):
            pass
        _SESSION_ENV.update(env)
    return _SESSION_ENV


def scope_cmd(cmd):
    """Run a launched program in its own systemd scope. Without this it would live inside the
    receiver's cgroup: restarting the receiver would close the app, and the app's memory and
    CPU would be counted as the receiver's."""
    runner = shutil.which("systemd-run")
    if runner is None:
        return cmd
    return [runner, "--user", "--scope", "--quiet", "--collect",
            "--description=CEC-Over-LAN launch"] + cmd


def launch(action, dry_run=False):
    """Start an installed application or open a URL. No shell, no free-form command."""
    if action["type"] == "app":
        path = desktop_path(action["id"])
        if path is None:
            print(f"[launch] unknown application {action['id']}", flush=True)
            return
        cmd, what = ["gio", "launch", str(path)], f"app {action['id']}"
    else:
        cmd, what = ["xdg-open", action["url"]], f"url {urlparse(action['url']).netloc}"
    print(f"[launch] {what}{' (dry-run)' if dry_run else ''}", flush=True)
    if dry_run:
        return
    try:
        proc = subprocess.Popen(scope_cmd(cmd), env=session_env(), start_new_session=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        threading.Thread(target=proc.wait, daemon=True).start()  # reap, no zombies
    except OSError as exc:
        print(f"[launch] failed: {exc}", flush=True)


def default_keymap(back_key="KEY_BACKSPACE"):
    return {
        "KEY_UP": {"tap": "KEY_UP"},
        "KEY_DOWN": {"tap": "KEY_DOWN"},
        "KEY_LEFT": {"tap": "KEY_LEFT"},
        "KEY_RIGHT": {"tap": "KEY_RIGHT"},
        "KEY_SELECT": {"tap": "KEY_ENTER"},
        "KEY_BACK": {"tap": back_key},
        "KEY_CHANNELUP": {"tap": "KEY_NEXTSONG"},
        "KEY_CHANNELDOWN": {"tap": "KEY_PREVIOUSSONG"},
        "KEY_SEARCH": {"tap": "KEY_PLAYPAUSE"},
        "KEY_STOPCD": {"tap": "KEY_HOMEPAGE"},
        # tap = GNOME overview (overlay-key 'Super')
        "KEY_SHUFFLE": {"tap": "KEY_LEFTMETA"},
        "KEY_EJECTCD": {"long": "KEY_LEFTALT+KEY_F4", "hold": DEFAULT_HOLD},
    }


APP_PATTERN = re.compile(r"^[^\x00-\x1f\x7f]{1,60}$")
MAX_APP_RULES = 10


def parse_entry(spec, label):
    """One mapping entry {tap, long, hold} -> (entry, error)."""
    if not isinstance(spec, dict):
        return None, f"{label}: invalid spec"
    tap = parse_action(spec["tap"]) if spec.get("tap") else None
    long_ = parse_action(spec["long"]) if spec.get("long") else None
    if spec.get("tap") and tap is None:
        return None, f"{label}: invalid tap action ({action_hint(spec['tap'])})"
    if spec.get("long") and long_ is None:
        return None, f"{label}: invalid long action ({action_hint(spec['long'])})"
    try:
        hold = float(spec.get("hold", DEFAULT_HOLD))
    except (TypeError, ValueError):
        return None, f"{label}: invalid hold time"
    if not 0.3 <= hold <= 10:
        return None, f"{label}: hold must be between 0.3 and 10 seconds"
    if not (tap or long_):
        return None, f"{label}: no action"
    return {"tap": tap, "long": long_, "hold": hold}, None


def normalise(raw):
    """Validate a raw {remote_key: spec} dict; returns (entries, errors).

    A spec may also carry "apps": {"<text>": {tap/long/hold}}: exceptions that replace the
    action while the focused window matches <text> (class, app id, title or process name,
    case-insensitive substring); the first matching exception wins."""
    entries, errors = {}, []
    if not isinstance(raw, dict):
        return entries, ["keys must be an object"]
    for key, spec in raw.items():
        if not (isinstance(key, str) and KEY_NAME.match(key)) or not isinstance(spec, dict):
            errors.append(f"{key}: invalid remote key or spec")
            continue
        entry, err = parse_entry(spec, key)
        if err:
            errors.append(err)
            continue
        entry["apps"] = []
        raw_apps = spec.get("apps") or {}
        if not isinstance(raw_apps, dict) or len(raw_apps) > MAX_APP_RULES:
            errors.append(f"{key}: apps must be an object with at most {MAX_APP_RULES} rules")
            continue
        bad = False
        for pattern, sub in raw_apps.items():
            sub_entry, sub_err = parse_entry(sub, f"{key} [{pattern}]")
            if not (isinstance(pattern, str) and APP_PATTERN.match(pattern.strip())) or sub_err:
                errors.append(sub_err or f"{key}: invalid app text {pattern!r}")
                bad = True
                break
            entry["apps"].append((pattern.strip(), sub_entry))
        if not bad:
            entries[key] = entry
    return entries, errors


class Keymap:
    """Mapping with automatic reload when the JSON file changes."""

    def __init__(self, path, back_key):
        self.path = Path(path)
        self.defaults = default_keymap(back_key)
        self.entries = {}
        self.mtime = "unset"
        self.checked = 0.0
        self.refresh(force=True)

    def refresh(self, force=False):
        now = time.monotonic()
        if not force and now - self.checked < 1.0:
            return
        self.checked = now
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            mtime = None
        if mtime == self.mtime:
            return
        self.mtime = mtime
        raw = self.defaults
        if mtime is not None:
            try:
                raw = json.loads(self.path.read_text()).get("keys", {})
            except (OSError, ValueError, AttributeError) as exc:
                print(f"[keymap] cannot read {self.path}: {exc}; keeping the previous mapping",
                      flush=True)
                return
        entries, errors = normalise(raw)
        for err in errors:
            print(f"[keymap] skipped: {err}", flush=True)
        self.entries = entries
        print(f"[keymap] loaded {len(entries)} keys from "
              f"{'file' if mtime is not None else 'defaults'}", flush=True)

    def get(self, key):
        self.refresh()
        return self.entries.get(key)


def key_codes():
    """Every KEY_* code, so the mapping can change without recreating the device."""
    codes = set()
    for code, name in e.keys.items():
        names = list(name) if isinstance(name, (list, tuple)) else [name]
        if code == 0 or any(n in DENY_TARGETS for n in names):
            continue
        if any(n.startswith("KEY_") for n in names):
            codes.add(code)
    return sorted(codes)


class FileFlag:
    """A runtime switch: it is ON while a flag file exists (the web UI creates/removes it).
    The file is re-checked at most every 0.5 s; a change is logged once."""

    def __init__(self, path, on_msg, off_msg):
        self.path = Path(path)
        self.on_msg, self.off_msg = on_msg, off_msg
        self._on = False
        self._last = None
        self._checked = -99.0

    @property
    def on(self):
        now = time.monotonic()
        if now - self._checked >= 0.5:
            self._checked = now
            self._on = self.path.exists()
            if self._on != self._last:
                self._last = self._on
                print(self.on_msg if self._on else self.off_msg, flush=True)
        return self._on


def DryFlag(path):
    return FileFlag(path, "[dry-run mode] ON: remote keys are only logged, nothing is applied",
                    "[dry-run mode] OFF: remote keys are applied")


def KeepPointerFlag(path):
    """ON = the owner wants the mouse pointer left alone (it is hidden by default)."""
    return FileFlag(path, "[pointer] ON: the mouse pointer is left where it is",
                    "[pointer] OFF: the mouse pointer is moved out of the way on remote presses")


class Out:
    def __init__(self, dry_run, flag=None):
        self.cli_dry = dry_run
        self.flag = flag
        self.pressed = set()  # keys really held down on the virtual device
        self.ui = None
        if not dry_run:
            if UInput is None:
                sys.exit("python-evdev is not available")
            self.ui = UInput({e.EV_KEY: key_codes()}, name="TV Remote (CEC-Over-LAN)")

    def is_dry(self):
        return self.cli_dry or bool(self.flag and self.flag.on)

    def key(self, name, value):
        # in dry-run nothing is pressed, but a key that is already down is still released
        if self.is_dry() and not (value == 0 and name in self.pressed):
            print(f"[dry-run] {name} {'DOWN' if value else 'UP'}", flush=True)
            return
        self.pressed.add(name) if value else self.pressed.discard(name)
        if self.ui is not None:
            self.ui.write(e.EV_KEY, getattr(e, name), value)
            self.ui.syn()


def make_pointer_device():
    """A virtual mouse used only to push the pointer out of the way; None if it cannot be made."""
    if UInput is None:
        return None
    try:
        return UInput({e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
                       e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL]},
                      name="TV Remote pointer mover (CEC-Over-LAN)")
    except OSError as exc:
        print(f"[pointer] cannot create the virtual mouse: {exc}", flush=True)
        return None


class Mouse:
    """Mouse actions on the virtual mouse (the same device that hides the pointer).

    A button is held while the remote button is held (a long press drags); movement and scrolling
    repeat while the remote button is held, movement getting faster the longer it is held."""

    TICK_S, SCROLL_TICK_S = 0.03, 0.12
    START, TOP, GAIN = 6, 40, 1.12   # pixels per tick: first step, fastest, growth per tick

    def __init__(self, out, ui):
        self.out, self.ui = out, ui
        self.running = {}   # remote key -> (stop Event, thread)
        self.buttons = {}   # remote key -> BTN_* held down

    def _dry(self):
        return self.out.is_dry()

    def _move(self, dx, dy):
        if dx:
            self.ui.write(e.EV_REL, e.REL_X, dx)
        if dy:
            self.ui.write(e.EV_REL, e.REL_Y, dy)
        self.ui.syn()

    def _scroll(self, direction):
        self.ui.write(e.EV_REL, e.REL_WHEEL, direction)
        self.ui.syn()

    def click(self, name):
        """One-shot: a click, one movement step or one scroll notch."""
        if self._dry() or self.ui is None:
            print(f"[mouse] would do {name}", flush=True)
            return
        if name in MOUSE_BUTTONS:
            btn = getattr(e, MOUSE_BUTTONS[name])
            self.ui.write(e.EV_KEY, btn, 1)
            self.ui.syn()
            self.ui.write(e.EV_KEY, btn, 0)
            self.ui.syn()
        elif name in MOUSE_MOVES:
            dx, dy = MOUSE_MOVES[name]
            self._move(dx * self.START * 3, dy * self.START * 3)
        else:
            self._scroll(MOUSE_SCROLL[name])

    def start(self, key, name):
        """The remote button went down."""
        if self._dry() or self.ui is None:
            print(f"[mouse] would start {name}", flush=True)
            return
        self.stop(key)
        if name in MOUSE_BUTTONS:
            btn = getattr(e, MOUSE_BUTTONS[name])
            self.ui.write(e.EV_KEY, btn, 1)
            self.ui.syn()
            self.buttons[key] = btn
            return
        stop = threading.Event()
        th = threading.Thread(target=self._repeat, args=(name, stop), daemon=True)
        self.running[key] = (stop, th)
        th.start()

    def _repeat(self, name, stop):
        step = float(self.START)
        while not stop.is_set():
            if name in MOUSE_MOVES:
                dx, dy = MOUSE_MOVES[name]
                self._move(dx * int(step), dy * int(step))
                step = min(self.TOP, step * self.GAIN)
                stop.wait(self.TICK_S)
            else:
                self._scroll(MOUSE_SCROLL[name])
                stop.wait(self.SCROLL_TICK_S)

    def stop(self, key):
        """The remote button went up (or the receiver is shutting down)."""
        running = self.running.pop(key, None)
        if running:
            running[0].set()
        btn = self.buttons.pop(key, None)
        if btn is not None and self.ui is not None:
            self.ui.write(e.EV_KEY, btn, 0)
            self.ui.syn()

    def stop_all(self):
        for key in list(self.running) + list(self.buttons):
            self.stop(key)


CORNERS = {"top-right": (1, -1), "bottom-right": (1, 1), "top-left": (-1, -1), "bottom-left": (-1, 1)}


class Pointer:
    """Hides the mouse pointer by moving it to a screen corner, where most of the cursor image
    hangs off the screen. Does nothing in dry run or when the owner wants the pointer.

    The corner matters: a bottom dock with auto-hide (dash-to-dock) reveals itself when the
    pointer pushes against the bottom edge and then never hides while the pointer is over it,
    and GNOME's hot corner is the top-left. So the default is the top-right corner."""

    STEP, REPEAT, MIN_GAP_S = 5000, 5, 0.4

    def __init__(self, out, keep_flag, ui, corner="top-right"):
        self.out, self.keep, self.ui = out, keep_flag, ui
        self.sx, self.sy = CORNERS[corner]
        self.last = -99.0

    def hide(self):
        """Returns what happened: moved | dry | kept | skipped."""
        if self.keep.on:
            return "kept"
        if self.out.is_dry():
            return "dry"
        now = time.monotonic()
        if self.ui is None or now - self.last < self.MIN_GAP_S:
            return "skipped"
        self.last = now
        for _ in range(self.REPEAT):
            self.ui.write(e.EV_REL, e.REL_X, self.sx * self.STEP)
            self.ui.write(e.EV_REL, e.REL_Y, self.sy * self.STEP)
            self.ui.syn()
        return "moved"


OSK_SCRIPT = Path(__file__).resolve().with_name("tv_osk.py")
OSK_SOCKET = "\0cec-over-lan-osk-%d" % os.getuid()  # abstract socket, same name as in tv_osk.py
OSK_NAV = {"KEY_UP": "up", "KEY_DOWN": "down", "KEY_LEFT": "left", "KEY_RIGHT": "right",
           "KEY_SELECT": "ok", "KEY_BACK": "backspace", "KEY_EXIT": "close"}


class OskController:
    """Starts and stops the on-screen keyboard (tv_osk.py) and, while it is open, sends it the
    remote's D-pad instead of the normal mapping. Its process lives in its own systemd scope."""

    def __init__(self, starter=None, sender=None):
        self.proc = None
        self.starter = starter or self._start
        self.sender = sender or self._send

    @property
    def active(self):
        return self.proc is not None and self.proc.poll() is None

    def toggle(self):
        if self.active:
            self.sender("close")
            return "closing"
        self.proc = self.starter()
        return "opening"

    def route(self, key):
        """While the keyboard is open the D-pad drives it. Returns the decision, or None."""
        cmd = OSK_NAV.get(key)
        if cmd is None or not self.active:
            return None
        self.sender(cmd)
        return "osk:" + cmd

    @staticmethod
    def _start():
        env = dict(session_env())
        env["GDK_BACKEND"] = "x11"  # an X11 window: Xwayland honours "never take the focus"
        return subprocess.Popen(scope_cmd([sys.executable, "-B", str(OSK_SCRIPT)]), env=env,
                                start_new_session=True, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @staticmethod
    def _send(msg):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            s.sendto(msg.encode("ascii"), OSK_SOCKET)
        except OSError:
            pass
        finally:
            s.close()


class Focus:
    """Which window has the keyboard focus, asked to the GNOME Shell extension "focuspid"
    (D-Bus org.claude.FocusPid, already installed on the machine). Cached for 0.25 s.
    current() is None when the extension is not there or does not answer."""

    def __init__(self, query=None, activate=None):
        self._val, self._at = None, -99.0
        self._query_fn = query or self._query
        self._activate_fn = activate or self._activate
        self.last = None   # the last window that really had the focus

    def current(self):
        now = time.monotonic()
        if now - self._at >= 0.25:
            self._at = now
            self._val = self._query_fn()
            if self._val and self._val["pid"]:
                self.last = self._val
        return self._val

    def restore_soon(self, delay=0.25):
        """Esc was just sent while no window has the focus (the dock after Ctrl+Alt+Tab, the
        overview, a shell menu): give the focus back to the window that had it before, so the
        arrow keys go to the application on screen again and not to GNOME. Returns True if a
        restore was scheduled."""
        v = self._val
        if v is None or v["pid"] or not self.last:
            return False
        threading.Timer(delay, self._restore, args=(self.last["pid"],)).start()
        return True

    def _restore(self, pid):
        print(f"[focus] focus returned to pid {pid}: {self._activate_fn(pid)}", flush=True)

    @staticmethod
    def _activate(pid):
        try:
            r = subprocess.run(
                ["gdbus", "call", "--session", "--dest", "org.claude.FocusPid",
                 "--object-path", "/org/claude/FocusPid", "--method", "org.claude.FocusPid.ActivatePid",
                 str(pid)], capture_output=True, text=True, timeout=1.5, env=session_env())
            return "true" in r.stdout
        except (subprocess.TimeoutExpired, OSError):
            return False

    @staticmethod
    def _query():
        try:
            r = subprocess.run(
                ["gdbus", "call", "--session", "--dest", "org.claude.FocusPid",
                 "--object-path", "/org/claude/FocusPid", "--method", "org.claude.FocusPid.Get"],
                capture_output=True, text=True, timeout=0.6, env=session_env())
            pid, cls, app_id, title = ast.literal_eval(r.stdout.strip())
        except (subprocess.TimeoutExpired, OSError, ValueError, SyntaxError):
            return None
        comm = ""
        if pid:
            try:
                comm = Path(f"/proc/{pid}/comm").read_text().strip()
            except OSError:
                pass
        return {"pid": pid, "class": cls, "app_id": app_id, "title": title, "comm": comm}


def focus_text(f):
    """Everything an app exception can match on, lower case."""
    return " ".join([f["class"], f["app_id"], f["title"], f["comm"]]).lower()


def focus_label(f):
    """Short, space-free name of the focused app for the log."""
    if not f:
        return "unknown"
    return re.sub(r"\s+", "_", f["class"] or f["app_id"] or f["comm"] or "none")[:30]


class Router:
    """Turns remote key events into injected keys / launches following the keymap."""

    def __init__(self, keymap, out, launcher=None, pointer=None, focus=None, osk=None, mouse=None):
        self.keymap = keymap
        self.out = out
        self.pointer = pointer
        self.focus = focus
        self.osk = osk
        self.mouse = mouse
        self.mouse_keys = set()  # remote keys whose mouse action is running
        self.active_entry = {}  # remote key -> the entry chosen at DOWN (used again at UP)
        self.launcher = launcher or (lambda action: launch(action, out.is_dry()))
        self.lock = threading.RLock()
        self.holds = {}   # remote key -> injected names currently held down
        self.timers = {}  # remote key -> pending long-press Timer
        self.last_launch = {}
        self.down_at = {}  # remote key -> monotonic time of the DOWN event (for the hold time)
        self.fired = set()  # remote keys whose long action already fired

    def _after_keys(self, names):
        """After an Esc: if no window has the focus, hand it back to the last one."""
        if self.focus is not None and "KEY_ESC" in names \
                and not getattr(self.out, "is_dry", lambda: False)():
            self.focus.restore_soon()

    # what Back and Exit usually send; with nothing focused they mean "close this overlay"
    DISMISS = {("KEY_ESC",), ("KEY_BACKSPACE",), ("KEY_LEFTALT", "KEY_F4")}

    def _dismiss_overlay(self, tap):
        """Backspace or Alt+F4 while no window has the focus (the dock after Ctrl+Alt+Tab, the
        overview): neither closes the dock, so send Esc instead, which also hands the focus back
        to the last window. Returns True if it did."""
        if tap["type"] != "keys" or tuple(tap["keys"]) not in self.DISMISS or self.focus is None:
            return False
        if tap["keys"] == ["KEY_ESC"] or getattr(self.out, "is_dry", lambda: False)():
            return False   # a plain Esc already takes the normal path
        v = getattr(self.focus, "_val", None)
        if v is None or v["pid"] or not getattr(self.focus, "last", None):
            return False
        self._click(["KEY_ESC"])
        return True

    def _click(self, names):
        for n in names:
            self.out.key(n, 1)
        for n in reversed(names):
            self.out.key(n, 0)
        self._after_keys(names)

    def _perform(self, action):
        """One-shot execution of an action (click for keys, launch for app/url)."""
        if action["type"] == "keys":
            self._click(action["keys"])
            return
        if action["type"] == "mouse":
            if self.mouse is not None:
                self.mouse.click(action["name"])
            return
        if action["type"] == "osk":
            if self.osk is None:
                return
            if getattr(self.out, "is_dry", lambda: False)():
                print("[osk] would open or close the on-screen keyboard", flush=True)
                return
            now = time.monotonic()
            if now - self.last_launch.get("osk", -99) < 0.8:
                return
            self.last_launch["osk"] = now
            print(f"[osk] {self.osk.toggle()}", flush=True)
            return
        text = action_to_text(action)
        now = time.monotonic()
        if now - self.last_launch.get(text, -99) < LAUNCH_DEBOUNCE_S:
            return
        self.last_launch[text] = now
        self.launcher(action)

    def _fire_long(self, key, action):
        with self.lock:
            if self.timers.pop(key, None) is not None:
                self.fired.add(key)
                self._perform(action)

    def on_event(self, key, state, active):
        """Handle one remote event and log it in a form the web UI's key monitor can parse:
        [seen] KEY_X DOWN screen=yes|no decision=<what will happen> dry=yes|no
        [seen] KEY_X UP   screen=yes|no held=<seconds> result=<what happened>"""
        now = time.monotonic()
        with self.lock:
            if state == "UP":
                held = now - self.down_at.pop(key, now)
                detail = f"held={held:.2f} result={self._up(key)}"
            else:
                self.down_at[key] = now
                # ask which window has the focus BEFORE acting: the app exceptions and the
                # focus restore after Esc both depend on the state at the moment of the press
                focused = self.focus.current() if active and self.focus is not None else None
                decision = self._down(key) if active else "off-screen"
                dry = "yes" if getattr(self.out, "is_dry", lambda: False)() else "no"
                detail = f"decision={decision} dry={dry}"
                if active and self.focus is not None:
                    detail += f" app={focus_label(focused)}"
                if active and self.pointer is not None:
                    # a mouse action would be undone by sending the pointer to the corner
                    detail += " pointer=mouse" if "mouse:" in decision else f" pointer={self.pointer.hide()}"
            print(f"[seen] {key} {state} screen={'yes' if active else 'no'} {detail}", flush=True)

    def _down(self, key):
        """Start handling a press; returns a short description of the decision."""
        if self.osk is not None:
            routed = self.osk.route(key)  # keyboard open: the D-pad drives it
            if routed:
                return routed
        entry = self.keymap.get(key)
        if not entry:
            return "unmapped"
        entry, rule = self._resolve(entry)
        self.active_entry[key] = entry
        suffix = "@" + re.sub(r"\s+", "_", rule) if rule else ""
        if entry["long"]:
            t = threading.Timer(entry["hold"], self._fire_long, args=(key, entry["long"]))
            self.timers[key] = t
            t.start()
            text = f"wait-long-{entry['hold']:g}s:{brief(entry['long'])}"
            if entry["tap"]:
                text += f"+tap-on-release:{brief(entry['tap'])}"
            return text + suffix
        if entry["tap"]:
            if self._dismiss_overlay(entry["tap"]):
                return "tap:esc-closes-overlay(" + brief(entry["tap"]) + ")" + suffix
            if entry["tap"]["type"] == "keys":
                for n in entry["tap"]["keys"]:
                    self.out.key(n, 1)
                self.holds[key] = entry["tap"]["keys"]
                self._after_keys(entry["tap"]["keys"])
            elif entry["tap"]["type"] == "mouse" and self.mouse is not None:
                self.mouse.start(key, entry["tap"]["name"])
                self.mouse_keys.add(key)
            else:
                self._perform(entry["tap"])
            return "tap:" + brief(entry["tap"]) + suffix
        return "none"

    def _resolve(self, entry):
        """Apply the app exceptions of an entry: returns (entry to use, matching text or None)."""
        if not entry.get("apps") or self.focus is None:
            return entry, None
        f = self.focus.current()
        if not f:
            return entry, None
        blob = focus_text(f)
        for pattern, override in entry["apps"]:
            if pattern.lower() in blob:
                return override, pattern
        return entry, None

    def _up(self, key):
        """Finish a press; returns what happened at release."""
        result = "released"
        if key in self.fired:
            self.fired.discard(key)
            result = "long-fired"
        t = self.timers.pop(key, None)
        entry = self.active_entry.pop(key, None) or self.keymap.get(key)
        if t is not None:  # released before the hold time
            t.cancel()
            if entry and entry["tap"]:
                self._perform(entry["tap"])
                result = "tap-clicked"
            else:
                result = "short-press-no-action"
        names = self.holds.pop(key, None)
        if names:
            for n in reversed(names):
                self.out.key(n, 0)
        if key in self.mouse_keys:
            self.mouse_keys.discard(key)
            self.mouse.stop(key)
        return result

    def release_all(self):
        with self.lock:
            for t in self.timers.values():
                t.cancel()
            self.timers.clear()
            for names in self.holds.values():
                for n in reversed(names):
                    self.out.key(n, 0)
            self.holds.clear()
            self.fired.clear()
            if self.mouse is not None:
                self.mouse.stop_all()
                self.mouse_keys.clear()


class Gate:
    """Tells whether the TV is showing this machine, from mActiveRoutingPath in hdmi_control."""

    def __init__(self, args, router):
        self.args = args
        self.router = router
        self.active = args.no_gate
        self.stop = threading.Event()
        self.fails = 0
        self.on_dead = None  # called when the TV link looks dead (set by run())

    def read_path(self):
        try:
            r = subprocess.run(
                ["adb", "-s", self.args.target, "shell",
                 "dumpsys hdmi_control | grep mActiveRoutingPath"],
                capture_output=True, text=True, timeout=6)
        except (subprocess.TimeoutExpired, OSError):
            return None
        m = PATH.search(r.stdout)
        return m.group(1).lower() if m else None

    def loop(self):
        want = self.args.active_path.lower()
        while not self.stop.is_set():
            path = self.read_path()
            # watchdog: if the TV stops answering (standby, reboot, port change) the adb link
            # can stay "open" forever; after a few failed reads force a reconnection
            self.fails = self.fails + 1 if path is None else 0
            if self.fails >= self.args.dead_after and self.on_dead:
                print(f"[watchdog] {self.fails} failed source reads, restarting the adb link",
                      flush=True)
                self.fails = 0
                self.on_dead()
            now = path == want  # if the read fails (None) -> do not inject
            if now != self.active:
                self.active = now
                if not now:
                    self.router.release_all()
                print(f"[source] path={path} -> machine on screen: {'yes' if now else 'no'}",
                      flush=True)
            self.stop.wait(self.args.poll)


ENV_FILE = STATE_DIR / "tv-remote.env"


def parse_avahi(text, service):
    """(host, port) pairs of the IPv4 entries that `avahi-browse -rpt <service>` printed."""
    found = []
    for line in text.splitlines():
        f = line.split(";")
        if len(f) >= 9 and f[0] == "=" and f[2] == "IPv4" and f[4] == service and f[8].isdigit():
            found.append((f[7], int(f[8])))
    return found


def parse_adb_mdns(text, service):
    found = []
    for line in text.splitlines():
        parts = line.split()
        if service in line and parts and ":" in parts[-1]:
            host, _, port = parts[-1].rpartition(":")
            if port.isdigit():
                found.append((host, int(port)))
    return found


def mdns_services(service, runner=subprocess.run):
    """Where the TV announces `service` (_adb-tls-connect._tcp or _adb-tls-pairing._tcp): the
    Wireless debugging port changes every time it is switched on, but it is announced over mDNS.
    Asks Avahi first (adb's own mDNS list is empty on many Linux setups), then adb."""
    for cmd, parse in ((["avahi-browse", "-rpt", service], parse_avahi),
                       (["adb", "mdns", "services"], parse_adb_mdns)):
        try:
            r = runner(cmd, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        found = parse(r.stdout, service)
        if found:
            return found
    return []


def discover_port(host, lookup=mdns_services):
    """The Wireless debugging connection port the TV at `host` announces now, or None."""
    for h, port in lookup("_adb-tls-connect._tcp"):
        if h == host:
            return port
    return None


def save_target(target, path=None):
    """Remember the new IP:port for the next start of the service (the web UI reads it too)."""
    path = Path(path or ENV_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"TV_TARGET={target}\n")
    os.replace(tmp, path)


def adb_connect(args, runner=subprocess.run, discover=discover_port, save=save_target):
    """Connect to args.target. If the TV refuses (Wireless debugging was switched off and on, so
    the port changed), find the new port over mDNS, switch to it and try once more.
    Returns True when connected."""
    for attempt in (0, 1):
        runner(["adb", "disconnect", args.target], capture_output=True, timeout=10)
        try:
            r = runner(["adb", "connect", args.target], capture_output=True, text=True, timeout=15)
            said = (r.stdout or "").strip()
        except subprocess.TimeoutExpired:   # a closed port that drops packets: no refusal, just silence
            said = ""
        if said.startswith(("connected", "already connected")):
            return True
        host = args.target.rpartition(":")[0]
        port = None if attempt else discover(host)
        if not port or f"{host}:{port}" == args.target:
            return False
        args.target = f"{host}:{port}"
        save(args.target)
        print(f"[adb] the TV now listens on port {port} (found over mDNS), saved for the next start", flush=True)
    return False


def run(args):
    keymap = Keymap(args.keymap, args.back_key)
    out = Out(args.dry_run, DryFlag(args.dryrun_file))
    pointer = None
    mouse_dev = None if args.dry_run else make_pointer_device()
    if not args.no_hide_pointer:
        pointer = Pointer(out, KeepPointerFlag(args.pointer_file), mouse_dev, args.pointer_corner)
    router = Router(keymap, out, pointer=pointer, focus=Focus(), osk=OskController(),
                    mouse=Mouse(out, mouse_dev))
    gate = Gate(args, router)
    current = {"proc": None}

    def kill_link():
        p = current["proc"]
        if p and p.poll() is None:
            p.kill()

    gate.on_dead = kill_link
    if not args.no_gate:
        threading.Thread(target=gate.loop, daemon=True).start()
    delay = 1
    try:
        while True:
            try:
                # clears any stale "offline" entry first; follows the TV when its port changed
                connected = adb_connect(args)
            except (subprocess.TimeoutExpired, OSError) as exc:
                print(f"adb connect failed ({type(exc).__name__}), retrying in {delay}s", flush=True)
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            if not connected:
                print(f"cannot reach {args.target}: is Wireless debugging on? retrying in {delay}s", flush=True)
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            proc = subprocess.Popen(
                ["adb", "-s", args.target, "shell", "getevent", "-l"],
                stdout=subprocess.PIPE, text=True, bufsize=1)
            current["proc"] = proc
            started = time.monotonic()
            for line in proc.stdout:
                m = LINE.search(line)
                if m:
                    router.on_event(m.group(1), m.group(2), gate.active)
            proc.wait()
            router.release_all()  # avoid stuck keys if adb drops
            if time.monotonic() - started > 30:
                delay = 1
            print(f"adb exited (code {proc.returncode}), retrying in {delay}s", flush=True)
            time.sleep(delay)
            delay = min(delay * 2, 30)
    except KeyboardInterrupt:
        pass
    finally:
        gate.stop.set()
        router.release_all()
        if out.ui:
            out.ui.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--target", required=True, help="IP:port of the wireless debugging connection")
    p.add_argument("--dry-run", action="store_true", help="print what would be injected/launched")
    p.add_argument("--keymap", default=str(KEYMAP_FILE), help="path of keymap.json")
    p.add_argument("--no-hide-pointer", action="store_true",
                   help="never move the mouse pointer out of the way on remote presses")
    p.add_argument("--pointer-corner", default="top-right", choices=sorted(CORNERS),
                   help="where the mouse pointer is parked (default top-right: a bottom dock reveals "
                        "itself when the pointer pushes against the bottom edge)")
    p.add_argument("--pointer-file", default=str(POINTER_FILE),
                   help="while this file exists the mouse pointer is left alone")
    p.add_argument("--dryrun-file", default=str(DRYRUN_FILE),
                   help="while this file exists nothing is pressed or launched, only logged")
    p.add_argument("--back-key", default="KEY_BACKSPACE", choices=["KEY_BACKSPACE", "KEY_ESC"],
                   help="Back key of the built-in default mapping")
    p.add_argument("--active-path", default="0x1000",
                   help="CEC routing path of the HDMI port of this machine (HDMI 1 = 0x1000)")
    p.add_argument("--poll", type=float, default=1.5, help="seconds between two source checks")
    p.add_argument("--no-gate", action="store_true", help="always inject")
    p.add_argument("--dead-after", type=int, default=4,
                   help="consecutive failed source reads before the adb link is restarted")
    run(p.parse_args())
