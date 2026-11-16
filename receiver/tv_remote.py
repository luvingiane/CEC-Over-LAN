#!/usr/bin/env python3
"""CEC-Over-LAN receiver for a Linux VM/PC.

Reads the key presses of a Google TV remote through
`adb shell getevent -l` ("variant A", no app on the TV) and re-injects them
into a uinput device, but ONLY while the TV is actually showing this machine
(the active CEC routing path equals the HDMI port the machine is plugged into).

Usage:
  tv_remote.py --target <IP:port> --dry-run     # print only, inject nothing
  tv_remote.py --target <IP:port>               # inject, only while the TV shows this machine
  tv_remote.py --target <IP:port> --no-gate     # always inject (no source check)
"""
import argparse
import re
import subprocess
import sys
import threading
import time

LINE = re.compile(r"/dev/input/event\d+:\s+EV_KEY\s+(KEY_\w+)\s+(DOWN|UP)\b")
PATH = re.compile(r"mActiveRoutingPath:\s*(0x[0-9a-fA-F]+)")
LONG_PRESS_S = 3.0

try:
    from evdev import UInput, ecodes as e
except ImportError:  # lets --dry-run work without evdev
    UInput = None
    e = None


def build_maps(back_key):
    """TV key -> injected key (evdev names)."""
    simple = {
        "KEY_UP": "KEY_UP",
        "KEY_DOWN": "KEY_DOWN",
        "KEY_LEFT": "KEY_LEFT",
        "KEY_RIGHT": "KEY_RIGHT",
        "KEY_SELECT": "KEY_ENTER",
        "KEY_BACK": back_key,
        "KEY_CHANNELUP": "KEY_NEXTSONG",
        "KEY_CHANNELDOWN": "KEY_PREVIOUSSONG",
        "KEY_SEARCH": "KEY_PLAYPAUSE",
        "KEY_STOPCD": "KEY_HOMEPAGE",
        "KEY_SHUFFLE": "KEY_LEFTMETA",  # tap = GNOME overview (overlay-key 'Super')
    }
    # TV key -> key combination, fired only after a long press
    long_combo = {"KEY_EJECTCD": ["KEY_LEFTALT", "KEY_F4"]}
    return simple, long_combo


class Out:
    def __init__(self, simple, long_combo, dry_run):
        self.dry = dry_run
        self.down = set()
        self.timers = {}
        self.lock = threading.RLock()
        names = set(simple.values())
        for combo in long_combo.values():
            names.update(combo)
        self.ui = None
        if not dry_run:
            if UInput is None:
                sys.exit("python-evdev is not available")
            caps = {e.EV_KEY: sorted(getattr(e, n) for n in names)}
            self.ui = UInput(caps, name="TV Remote (CEC-Over-LAN)")

    def key(self, name, value):
        if self.dry:
            print(f"[dry-run] {name} {'DOWN' if value else 'UP'}", flush=True)
            return
        self.ui.write(e.EV_KEY, getattr(e, name), value)
        self.ui.syn()

    def press(self, name):
        with self.lock:
            self.down.add(name)
            self.key(name, 1)

    def release(self, name):
        with self.lock:
            self.down.discard(name)
            self.key(name, 0)

    def release_all(self):
        with self.lock:
            for t in self.timers.values():
                t.cancel()
            self.timers.clear()
            for n in list(self.down):
                self.release(n)

    def combo(self, names):
        with self.lock:
            for n in names:
                self.press(n)
            for n in reversed(names):
                self.release(n)


class Gate:
    """Tells whether the TV is showing this machine, from mActiveRoutingPath in hdmi_control."""

    def __init__(self, args, out):
        self.args = args
        self.out = out
        self.active = args.no_gate
        self.stop = threading.Event()

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
            now = path == want  # if the read fails (None) -> do not inject
            if now != self.active:
                self.active = now
                if not now:
                    self.out.release_all()
                print(f"[source] path={path} -> machine on screen: {'yes' if now else 'no'}",
                      flush=True)
            self.stop.wait(self.args.poll)


def handle(out, simple, long_combo, key, state):
    if key in simple:
        (out.press if state == "DOWN" else out.release)(simple[key])
    elif key in long_combo:
        if state == "DOWN":
            t = threading.Timer(LONG_PRESS_S, out.combo, args=(long_combo[key],))
            out.timers[key] = t
            t.start()
        else:
            t = out.timers.pop(key, None)
            if t:
                t.cancel()
    else:
        print(f"[ignored] {key} {state}", flush=True)


def run(args):
    simple, long_combo = build_maps(args.back_key)
    out = Out(simple, long_combo, args.dry_run)
    gate = Gate(args, out)
    if not args.no_gate:
        threading.Thread(target=gate.loop, daemon=True).start()
    delay = 1
    try:
        while True:
            try:
                subprocess.run(["adb", "connect", args.target], capture_output=True, timeout=15)
            except (subprocess.TimeoutExpired, OSError) as exc:
                print(f"adb connect failed ({type(exc).__name__}), retrying in {delay}s", flush=True)
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            proc = subprocess.Popen(
                ["adb", "-s", args.target, "shell", "getevent", "-l"],
                stdout=subprocess.PIPE, text=True, bufsize=1)
            started = time.monotonic()
            for line in proc.stdout:
                m = LINE.search(line)
                if m and gate.active:
                    handle(out, simple, long_combo, m.group(1), m.group(2))
            proc.wait()
            out.release_all()  # avoid stuck keys if adb drops
            if time.monotonic() - started > 30:
                delay = 1
            print(f"adb exited (code {proc.returncode}), retrying in {delay}s", flush=True)
            time.sleep(delay)
            delay = min(delay * 2, 30)
    except KeyboardInterrupt:
        pass
    finally:
        gate.stop.set()
        out.release_all()
        if out.ui:
            out.ui.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--target", required=True, help="IP:port of the wireless debugging connection")
    p.add_argument("--dry-run", action="store_true", help="print what would be injected")
    p.add_argument("--back-key", default="KEY_BACKSPACE", choices=["KEY_BACKSPACE", "KEY_ESC"])
    p.add_argument("--active-path", default="0x1000",
                   help="CEC routing path of the HDMI port of this machine (HDMI 1 = 0x1000)")
    p.add_argument("--poll", type=float, default=1.5, help="seconds between two source checks")
    p.add_argument("--no-gate", action="store_true", help="always inject")
    run(p.parse_args())
