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
  tv_remote.py --target <IP:port> --dry-run     # print only, inject nothing
  tv_remote.py --target <IP:port>               # inject, only while the TV shows this machine
  tv_remote.py --target <IP:port> --no-gate     # always inject (no source check)

keymap.json format:
  {"version": 1, "keys": {
      "KEY_SELECT":  {"tap": "KEY_ENTER"},
      "KEY_EJECTCD": {"long": "KEY_LEFTALT+KEY_F4", "hold": 3.0},
      "KEY_8":       {"tap": "KEY_8", "long": "KEY_LEFTCTRL+KEY_8", "hold": 1.0}}}
  "tap": key or "+"-joined combo, pressed on DOWN and released on UP (or clicked
         on release when a "long" action also exists and the hold time was not reached).
  "long": combo fired once after "hold" seconds (default 3).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

LINE = re.compile(r"/dev/input/event\d+:\s+EV_KEY\s+(KEY_\w+)\s+(DOWN|UP)\b")
PATH = re.compile(r"mActiveRoutingPath:\s*(0x[0-9a-fA-F]+)")
KEY_NAME = re.compile(r"^KEY_[A-Z0-9_]+$")
STATE_DIR = Path(os.environ.get("CEC_STATE_DIR", Path.home() / "cec-over-lan"))
KEYMAP_FILE = STATE_DIR / "keymap.json"
DEFAULT_HOLD = 3.0
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


def parse_combo(text):
    """'KEY_LEFTALT+KEY_F4' -> ['KEY_LEFTALT', 'KEY_F4'], or None if invalid/empty."""
    if not isinstance(text, str):
        return None
    names = [p.strip() for p in text.split("+") if p.strip()]
    if not names or len(names) > 4 or not all(valid_target(n) for n in names):
        return None
    return names


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


def normalise(raw):
    """Validate a raw {remote_key: spec} dict; returns (entries, errors)."""
    entries, errors = {}, []
    if not isinstance(raw, dict):
        return entries, ["keys must be an object"]
    for key, spec in raw.items():
        if not (isinstance(key, str) and KEY_NAME.match(key)) or not isinstance(spec, dict):
            errors.append(f"{key}: invalid remote key or spec")
            continue
        tap = parse_combo(spec["tap"]) if spec.get("tap") else None
        long_ = parse_combo(spec["long"]) if spec.get("long") else None
        if spec.get("tap") and tap is None:
            errors.append(f"{key}: invalid tap action")
            continue
        if spec.get("long") and long_ is None:
            errors.append(f"{key}: invalid long action")
            continue
        try:
            hold = float(spec.get("hold", DEFAULT_HOLD))
        except (TypeError, ValueError):
            errors.append(f"{key}: invalid hold time")
            continue
        if not 0.3 <= hold <= 10:
            errors.append(f"{key}: hold must be between 0.3 and 10 seconds")
            continue
        if not (tap or long_):
            errors.append(f"{key}: no action")
            continue
        entries[key] = {"tap": tap, "long": long_, "hold": hold}
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


class Out:
    def __init__(self, dry_run):
        self.dry = dry_run
        self.ui = None
        if not dry_run:
            if UInput is None:
                sys.exit("python-evdev is not available")
            self.ui = UInput({e.EV_KEY: key_codes()}, name="TV Remote (CEC-Over-LAN)")

    def key(self, name, value):
        if self.dry:
            print(f"[dry-run] {name} {'DOWN' if value else 'UP'}", flush=True)
            return
        self.ui.write(e.EV_KEY, getattr(e, name), value)
        self.ui.syn()


class Router:
    """Turns remote key events into injected keys following the keymap."""

    def __init__(self, keymap, out):
        self.keymap = keymap
        self.out = out
        self.lock = threading.RLock()
        self.holds = {}   # remote key -> injected names currently held down
        self.timers = {}  # remote key -> pending long-press Timer
        self.fired = set()  # remote keys whose long action already fired

    def _click(self, names):
        for n in names:
            self.out.key(n, 1)
        for n in reversed(names):
            self.out.key(n, 0)

    def _fire_long(self, key, names):
        with self.lock:
            if self.timers.pop(key, None) is not None:
                self.fired.add(key)
                self._click(names)

    def on_event(self, key, state, active):
        print(f"[seen] {key} {state}", flush=True)
        with self.lock:
            if state == "UP":
                self._up(key)
            elif active:
                self._down(key)

    def _down(self, key):
        entry = self.keymap.get(key)
        if not entry:
            return
        self.fired.discard(key)
        if entry["long"]:
            t = threading.Timer(entry["hold"], self._fire_long, args=(key, entry["long"]))
            self.timers[key] = t
            t.start()
        elif entry["tap"]:
            for n in entry["tap"]:
                self.out.key(n, 1)
            self.holds[key] = entry["tap"]

    def _up(self, key):
        t = self.timers.pop(key, None)
        if t is not None:  # released before the hold time
            t.cancel()
            entry = self.keymap.get(key)
            if entry and entry["tap"]:
                self._click(entry["tap"])
        names = self.holds.pop(key, None)
        if names:
            for n in reversed(names):
                self.out.key(n, 0)
        self.fired.discard(key)

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


def run(args):
    keymap = Keymap(args.keymap, args.back_key)
    out = Out(args.dry_run)
    router = Router(keymap, out)
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
                # clear any stale "offline" entry before reconnecting
                subprocess.run(["adb", "disconnect", args.target], capture_output=True, timeout=10)
                subprocess.run(["adb", "connect", args.target], capture_output=True, timeout=15)
            except (subprocess.TimeoutExpired, OSError) as exc:
                print(f"adb connect failed ({type(exc).__name__}), retrying in {delay}s", flush=True)
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
    p.add_argument("--dry-run", action="store_true", help="print what would be injected")
    p.add_argument("--keymap", default=str(KEYMAP_FILE), help="path of keymap.json")
    p.add_argument("--back-key", default="KEY_BACKSPACE", choices=["KEY_BACKSPACE", "KEY_ESC"],
                   help="Back key of the built-in default mapping")
    p.add_argument("--active-path", default="0x1000",
                   help="CEC routing path of the HDMI port of this machine (HDMI 1 = 0x1000)")
    p.add_argument("--poll", type=float, default=1.5, help="seconds between two source checks")
    p.add_argument("--no-gate", action="store_true", help="always inject")
    p.add_argument("--dead-after", type=int, default=4,
                   help="consecutive failed source reads before the adb link is restarted")
    run(p.parse_args())
