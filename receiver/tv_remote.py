#!/usr/bin/env python3
"""Receiver per la VM: legge i tasti del telecomando Google TV via
`adb shell getevent -l` (variante A) e li re-inietta in un device uinput,
ma SOLO quando la TV sta mostrando la VM (percorso CEC attivo = quello della
porta HDMI a cui e' collegata la VM).

Uso:
  tv_remote.py --target <IP:porta> --dry-run     # stampa soltanto, non inietta
  tv_remote.py --target <IP:porta>               # inietta davvero
  tv_remote.py --target <IP:porta> --no-gate     # inietta sempre (senza controllo sorgente)
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
except ImportError:  # permette --dry-run senza evdev
    UInput = None
    e = None


def build_maps(back_key):
    """tasto TV -> tasto iniettato (nomi evdev)."""
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
        "KEY_SHUFFLE": "KEY_LEFTMETA",  # tocco = panoramica GNOME (overlay-key 'Super')
    }
    # tasto TV -> combinazione, scatta solo dopo pressione lunga
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
                sys.exit("python-evdev non disponibile")
            caps = {e.EV_KEY: sorted(getattr(e, n) for n in names)}
            self.ui = UInput(caps, name="TV Remote (vm170)")

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
    """Dice se la TV sta mostrando la VM, leggendo mActiveRoutingPath da hdmi_control."""

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
            now = path == want  # se la lettura fallisce (None) -> non iniettare
            if now != self.active:
                self.active = now
                if not now:
                    self.out.release_all()
                print(f"[sorgente] percorso={path} -> VM sullo schermo: {'si' if now else 'no'}",
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
        print(f"[ignorato] {key} {state}", flush=True)


def run(args):
    simple, long_combo = build_maps(args.back_key)
    out = Out(simple, long_combo, args.dry_run)
    gate = Gate(args, out)
    if not args.no_gate:
        threading.Thread(target=gate.loop, daemon=True).start()
    delay = 1
    try:
        while True:
            subprocess.run(["adb", "connect", args.target], capture_output=True, timeout=15)
            proc = subprocess.Popen(
                ["adb", "-s", args.target, "shell", "getevent", "-l"],
                stdout=subprocess.PIPE, text=True, bufsize=1)
            started = time.monotonic()
            for line in proc.stdout:
                m = LINE.search(line)
                if m and gate.active:
                    handle(out, simple, long_combo, m.group(1), m.group(2))
            proc.wait()
            out.release_all()  # evita tasti incastrati se adb cade
            if time.monotonic() - started > 30:
                delay = 1
            print(f"adb terminato (codice {proc.returncode}), riprovo tra {delay}s", flush=True)
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
    p.add_argument("--target", required=True, help="IP:porta del debug wireless")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--back-key", default="KEY_BACKSPACE", choices=["KEY_BACKSPACE", "KEY_ESC"])
    p.add_argument("--active-path", default="0x1000",
                   help="percorso CEC della porta HDMI della VM (HDMI 1 = 0x1000)")
    p.add_argument("--poll", type=float, default=1.5, help="secondi tra due controlli sorgente")
    p.add_argument("--no-gate", action="store_true", help="inietta sempre")
    run(p.parse_args())
