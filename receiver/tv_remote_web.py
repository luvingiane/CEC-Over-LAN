#!/usr/bin/env python3
"""Small LAN web UI to pair/connect adb with the TV from a phone.

Why: after a TV reboot or standby the wireless debugging port and pairing are
lost (see REPORT.md, T4/T6). Doing the pairing on the machine that is shown on
the TV is awkward, so this serves a mobile-friendly page instead.

Security: answers only to private addresses, every API call needs a token
(generated on first start, stored with mode 0600). Never expose it to the
internet. No shell is used: adb is called with an argument list and all inputs
are validated.

  tv_remote_web.py [--bind 0.0.0.0] [--port 8765]
"""
import argparse
import hmac
import ipaddress
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tv_remote as core  # noqa: E402  (validation and defaults shared with the receiver)

STATE_DIR = Path(os.environ.get("CEC_STATE_DIR", Path.home() / "cec-over-lan"))
ENV_FILE = STATE_DIR / "tv-remote.env"
TOKEN_FILE = STATE_DIR / "token"
SERVICE = os.environ.get("CEC_SERVICE", "tv-remote")
CODE = re.compile(r"^\d{6}$")
LOCK = threading.Lock()  # one adb pair/connect at a time


def load_token():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    token = secrets.token_urlsafe(16)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    return token


def run(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except OSError as exc:
        return 127, str(exc)


def valid_host(value):
    try:
        ip = ipaddress.IPv4Address(value)
    except ValueError:
        return False
    return ip.is_private


def valid_port(value):
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535


def read_target():
    try:
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith("TV_TARGET="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def write_target(target):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = ENV_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"TV_TARGET={target}\n")
    os.replace(tmp, ENV_FILE)


def status(_q=None):
    target = read_target()
    _, active = run(["systemctl", "--user", "is-active", SERVICE], 5)
    link = "unknown"
    if target:
        code, out = run(["adb", "-s", target, "get-state"], 5)
        link = out if code == 0 and out else "unreachable"
    on_screen = "unknown"
    _, journal = run(["journalctl", "--user", "-u", SERVICE, "-n", "40", "-o", "cat",
                      "--no-pager"], 5)
    for line in journal.splitlines():
        if line.startswith("[source]"):
            on_screen = "yes" if line.endswith("yes") else "no"
    _, log = run(["journalctl", "--user", "-u", SERVICE, "-n", "200", "-o", "cat", "--no-pager"], 5)
    would = [ln for ln in log.splitlines()
             if (ln.startswith("[dry-run] ") or ln.startswith("[launch] ")) and "mode" not in ln]
    return {"target": target, "service": active, "link": link, "on_screen": on_screen,
            "dry_run": core.DRYRUN_FILE.exists(), "would": would[-10:],
            "hide_pointer": not core.POINTER_FILE.exists()}


def discover(_q=None):
    """Fills in the ports from the mDNS announcements of the TV (Avahi, then adb's own list). Both
    Wireless debugging screens announce: the main one (connect) always, the pairing dialog only
    while it is open."""
    found = {"host": "", "pair_port": 0, "connect_port": 0}
    for host, port in core.mdns_services("_adb-tls-pairing._tcp"):
        if valid_host(host):
            found["host"], found["pair_port"] = host, port
    for host, port in core.mdns_services("_adb-tls-connect._tcp"):
        if valid_host(host) and (not found["host"] or host == found["host"]):
            found["host"], found["connect_port"] = host, port
    ok = bool(found["host"])
    return {"ok": ok, "found": found,
            "message": "ports filled in" if ok else "nothing announced (is Wireless debugging on? or enter the values by hand)"}


def do_pair(data):
    host, port, code = data.get("host", ""), data.get("port"), str(data.get("code", ""))
    if not valid_host(host) or not valid_port(port) or not CODE.match(code):
        return {"ok": False, "message": "invalid host, port or code"}
    with LOCK:
        rc, out = run(["adb", "pair", f"{host}:{port}", code], 25)
    return {"ok": rc == 0 and "Successfully paired" in out,
            "message": out.replace(code, "******")}


def do_connect(data):
    host, port = data.get("host", ""), data.get("port")
    if not valid_host(host) or not valid_port(port):
        return {"ok": False, "message": "invalid host or port"}
    target = f"{host}:{port}"
    with LOCK:
        run(["adb", "disconnect", target], 10)
        rc, out = run(["adb", "connect", target], 20)
        ok = rc == 0 and out.startswith(("connected", "already connected"))
        if ok:
            write_target(target)
            run(["systemctl", "--user", "restart", SERVICE], 15)
    return {"ok": ok, "message": out or "no answer from the TV"}


def do_service(data):
    action = data.get("action")
    if action not in ("start", "stop", "restart"):
        return {"ok": False, "message": "invalid action"}
    rc, out = run(["systemctl", "--user", action, SERVICE], 15)
    return {"ok": rc == 0, "message": out or action + " done"}


KEYMAP_FILE = Path(os.environ.get("CEC_KEYMAP", core.KEYMAP_FILE))
SEEN = re.compile(r"^\[seen\] (KEY_[A-Z0-9_]+) DOWN\b")


def key_names():
    """Names a key can be mapped to (datalist in the page)."""
    if core.e is not None:
        names = set()
        for value in core.e.keys.values():
            for n in (value if isinstance(value, (list, tuple)) else [value]):
                if n.startswith("KEY_") and core.valid_target(n):
                    names.add(n)
        return sorted(names)
    base = [f"KEY_{c}" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"]
    return sorted(set(base) | {"KEY_ENTER", "KEY_ESC", "KEY_BACKSPACE", "KEY_TAB", "KEY_SPACE",
                               "KEY_UP", "KEY_DOWN", "KEY_LEFT", "KEY_RIGHT", "KEY_HOMEPAGE"})


def entry_to_file(spec):
    item = {}
    if spec["tap"]:
        item["tap"] = core.action_to_text(spec["tap"])
    if spec["long"]:
        item["long"] = core.action_to_text(spec["long"])
        item["hold"] = spec["hold"]
    return item


def to_file_format(entries):
    out = {}
    for key, spec in entries.items():
        item = entry_to_file(spec)
        if spec.get("apps"):
            item["apps"] = {pattern: entry_to_file(sub) for pattern, sub in spec["apps"]}
        out[key] = item
    return out


def parse_desktop(path):
    """Name of a launchable application from a .desktop file, or None."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    name, kind, in_entry = None, None, False
    extra = {"icon": "", "wm": "", "exec": ""}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            in_entry = line == "[Desktop Entry]"
            continue
        if not in_entry or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k == "Name":
            name = v
        elif k == "Icon":
            extra["icon"] = v.strip()
        elif k == "StartupWMClass":
            extra["wm"] = v.strip()
        elif k == "Exec":
            extra["exec"] = v.strip()
        elif k == "Type":
            kind = v
        elif k in ("NoDisplay", "Hidden") and v.strip().lower() == "true":
            return None
    if kind != "Application" or not name:
        return None
    return {"id": path.name, "name": name, **extra}


ICON_ROOTS = [Path.home() / ".local/share/icons", Path("/usr/share/icons"),
              Path.home() / ".local/share/flatpak/exports/share/icons",
              Path("/var/lib/flatpak/exports/share/icons")]
ICON_SIZES = ["48x48", "64x64", "128x128", "256x256", "32x32", "scalable"]
ICON_TYPES = {".png": "image/png", ".svg": "image/svg+xml"}
ICON_MAX = 60000  # bytes: bigger files stay out of the list


def find_icon(name):
    """Path of the file for a .desktop Icon= value, or None."""
    if not name:
        return None
    if name.startswith("/"):
        p = Path(name)
        return p if p.suffix in ICON_TYPES and p.is_file() else None
    for root in ICON_ROOTS:
        for size in ICON_SIZES:
            for ext in (".png", ".svg"):
                p = root / "hicolor" / size / "apps" / (name + ext)
                if p.is_file():
                    return p
    for ext in (".png", ".svg"):
        p = Path("/usr/share/pixmaps") / (name + ext)
        if p.is_file():
            return p
    return None


def icon_data(name):
    """The icon as a data: URI (the page cannot send the token with an <img>), or ''."""
    import base64
    p = find_icon(name)
    if not p:
        return ""
    try:
        raw = p.read_bytes()
    except OSError:
        return ""
    if len(raw) > ICON_MAX:
        return ""
    return f"data:{ICON_TYPES[p.suffix]};base64," + base64.b64encode(raw).decode()


def list_apps(_q=None):
    """Installed applications that can be launched from a key (.desktop files)."""
    apps = {}
    for d in core.APP_DIRS:
        try:
            files = sorted(d.glob("*.desktop"))
        except OSError:
            continue
        for p in files:
            if p.name not in apps and core.DESKTOP_ID.match(p.name):
                info = parse_desktop(p)
                if info:
                    info["icon"] = icon_data(info["icon"])
                    apps[p.name] = info
    return {"ok": True, "apps": sorted(apps.values(), key=lambda a: a["name"].lower())}


EVENT = re.compile(r"^(\d+\.\d+)\s+\S+\s+\S+?:\s+\[seen\] (KEY_[A-Z0-9_]+) DOWN\b")
EVENT_FILE_LINE = re.compile(r"^(\d+\.\d+)\s+\[seen\] (KEY_[A-Z0-9_]+) DOWN\b")
MON = re.compile(r"^(\d+\.\d+)\s+\S+\s+\S+?:\s+\[seen\] (KEY_[A-Z0-9_]+) (DOWN|UP)\b(.*)$")
MON_FILE_LINE = re.compile(r"^(\d+\.\d+)\s+\[seen\] (KEY_[A-Z0-9_]+) (DOWN|UP)\b(.*)$")
FIELD = re.compile(r"(\w+)=(\S+)")


def monitor(q):
    """Every remote event the receiver saw after a timestamp, with what it decided to do."""
    try:
        after = float(q.get("after", [time.time() - 60])[0])
    except (ValueError, IndexError):
        after = time.time() - 60
    test_file = os.environ.get("CEC_EVENTS_FILE")  # test hook, same file as /api/events
    if test_file:
        try:
            lines = Path(test_file).read_text().splitlines()
        except OSError:
            lines = []
        pattern = MON_FILE_LINE
    else:
        _, out = run(["journalctl", "--user", "-u", SERVICE, "--since", f"@{int(after)}",
                      "-o", "short-unix", "--no-pager"], 5)
        lines, pattern = out.splitlines(), MON
    found = []
    for line in lines:
        m = pattern.match(line.strip())
        if not m or float(m.group(1)) <= after:
            continue
        f = dict(FIELD.findall(m.group(4)))
        found.append({
            "ts": float(m.group(1)), "key": m.group(2), "state": m.group(3),
            "screen": None if "screen" not in f else f["screen"] == "yes",
            "decision": f.get("decision"), "dry": None if "dry" not in f else f["dry"] == "yes",
            "held": float(f["held"]) if "held" in f else None, "result": f.get("result"),
            "app": f.get("app")})
    return {"ok": True, "now": time.time(), "dry_run": core.DRYRUN_FILE.exists(),
            "events": found[-200:]}


def events(q):
    """Key presses seen by the receiver after a given server timestamp (wizard)."""
    try:
        after = float(q.get("after", [time.time() - 5])[0])
    except (ValueError, IndexError):
        after = time.time() - 5
    found = []
    test_file = os.environ.get("CEC_EVENTS_FILE")  # test hook: "<ts> [seen] KEY_X DOWN" lines
    if test_file:
        try:
            lines = Path(test_file).read_text().splitlines()
        except OSError:
            lines = []
        pattern = EVENT_FILE_LINE
    else:
        _, out = run(["journalctl", "--user", "-u", SERVICE, "--since", f"@{int(after)}",
                      "-o", "short-unix", "--no-pager"], 5)
        lines, pattern = out.splitlines(), EVENT
    for line in lines:
        m = pattern.match(line.strip())
        if m and float(m.group(1)) > after:
            found.append({"ts": float(m.group(1)), "key": m.group(2)})
    return {"ok": True, "now": time.time(), "events": found[-50:]}


def profiles(_q=None):
    """Lists of commands (ES-DE, desktop/media, numbers) used by the setup wizard."""
    try:
        return {"ok": True, **json.loads((HERE / "web" / "profiles.json").read_text())}
    except (OSError, ValueError):
        return {"ok": False, "message": "profiles.json is missing or broken", "profiles": []}


LEARNED_FILE = STATE_DIR / "remote-learned.json"


def _read_learned():
    try:
        data = json.loads(LEARNED_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


CUSTOM_REMOTE_FILE = STATE_DIR / "remote.json"   # the owner's own remote, generated with REMOTE-PROMPT.md
BUILTIN_REMOTE_FILE = HERE / "web" / "remote.json"
PROMPT_FILE = HERE / "web" / "remote-prompt.md"
BUTTON_ID = re.compile(r"^[a-z0-9_]{1,24}$")
SHAPES = {"round": "round", "small": "pill", "wide": "pill", "color": "col"}   # shape -> glyph kind
COLORS = {"red", "green", "yellow", "blue"}


def validate_remote(model):
    """None if the model of a remote is usable, otherwise a sentence saying what is wrong."""
    if not isinstance(model, dict) or not isinstance(model.get("buttons"), dict):
        return 'the JSON needs a "buttons" object'
    buttons = model["buttons"]
    if not 1 <= len(buttons) <= 80:
        return "between 1 and 80 buttons"
    codes = {}
    for bid, b in buttons.items():
        if not BUTTON_ID.match(bid) or not isinstance(b, dict):
            return f"button id {bid!r}: lower-case letters, digits and _ only (max 24)"
        if not isinstance(b.get("label"), str) or not 1 <= len(b["label"]) <= 12:
            return f'{bid}: "label" is text of 1 to 12 characters'
        if not isinstance(b.get("title", ""), str) or len(b.get("title", "")) > 40:
            return f'{bid}: "title" is text of at most 40 characters'
        code = b.get("code")
        if code is not None:
            if not isinstance(code, str) or not core.KEY_NAME.match(code):
                return f'{bid}: "code" is null or a name like KEY_VOLUMEUP'
            if code in codes:
                return f'{bid} and {codes[code]} have the same code {code}'
            codes[code] = bid
        shape = b.get("shape", "round")
        if shape not in SHAPES:
            return f'{bid}: "shape" is one of {", ".join(sorted(SHAPES))}'
        if shape == "color" and b.get("color") not in COLORS:
            return f'{bid}: a "color" button needs "color": one of {", ".join(sorted(COLORS))}'
    layout = model.get("layout")
    if layout is None:
        return None
    if not isinstance(layout, list) or not 1 <= len(layout) <= 30:
        return '"layout" is a list of 1 to 30 rows'
    used = set()

    def use(bid):
        if bid not in buttons:
            return f'the layout uses "{bid}", which is not in "buttons"'
        if bid in used:
            return f'"{bid}" appears twice in the layout'
        used.add(bid)
        return None

    for row in layout:
        if not isinstance(row, dict):
            return "every layout row is an object"
        if "dpad" in row:
            d = row["dpad"]
            if not isinstance(d, dict) or set(d) != {"up", "down", "left", "right", "ok"}:
                return '"dpad" needs exactly up, down, left, right and ok'
            ids = list(d.values())
        else:
            items = row.get("items")
            if not isinstance(items, list) or not 1 <= len(items) <= 6:
                return 'a row needs "items": 1 to 6 slots'
            if row.get("align", "center") not in ("center", "between"):
                return '"align" is center or between'
            ids = []
            for it in items:
                if isinstance(it, str):
                    ids.append(it)
                elif isinstance(it, dict) and len(it) == 1 and next(iter(it)) in ("rocker", "stack"):
                    pair = next(iter(it.values()))
                    if not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(x, str) for x in pair):
                        return '"rocker" and "stack" take exactly two button ids'
                    ids += pair
                else:
                    return 'a slot is a button id, {"rocker": [a, b]} or {"stack": [a, b]}'
        for bid in ids:
            if not isinstance(bid, str):
                return "button ids are text"
            err = use(bid)
            if err:
                return err
    return None


def button_kinds(model):
    """Adds to every button how it is drawn as a glyph: kind round|pill|col and color."""
    kinds = {}
    for row in model.get("layout") or []:
        if "dpad" in row:
            kinds.update({i: "round" for i in row["dpad"].values()})
        for it in row.get("items", []):
            if isinstance(it, dict) and "rocker" in it:
                kinds.update({i: "round" for i in it["rocker"]})
            elif isinstance(it, dict) and "stack" in it:
                kinds.update({i: "pill" for i in it["stack"]})
    for bid, b in model["buttons"].items():
        shape = b.get("shape", "round")
        kind = SHAPES[shape] if shape != "round" else kinds.get(bid, "round")
        b["kind"] = kind
        b["color"] = b.get("color", "") if kind == "col" else ""


def _load_remote():
    """(model, custom?) : the owner's own remote if it is there and valid, else the built-in one."""
    try:
        own = json.loads(CUSTOM_REMOTE_FILE.read_text())
        if validate_remote(own) is None:
            return own, True
    except (OSError, ValueError):
        pass
    return json.loads(BUILTIN_REMOTE_FILE.read_text()), False


def remote_model():
    """The physical remote: its buttons and the key code each one sends (learned codes win)."""
    try:
        model, custom = _load_remote()
    except (OSError, ValueError):
        return {"ok": False, "message": "remote.json is missing or broken", "buttons": {}}
    for bid, code in _read_learned().items():
        btn = model["buttons"].get(bid)
        if btn is not None and isinstance(code, str) and core.KEY_NAME.match(code):
            btn["code"], btn["learned"] = code, True
            btn.pop("guess", None)
    button_kinds(model)
    return {"ok": True, "custom": custom, **model}


def do_remote_model(data):
    """Use a remote generated by the owner's AI ({"model": {...}}) or go back to the built-in one
    ({"reset": true})."""
    if data.get("reset") is True:
        try:
            CUSTOM_REMOTE_FILE.unlink()
        except FileNotFoundError:
            pass
        return {"ok": True, "message": "back to the built-in remote"}
    model = data.get("model")
    if isinstance(model, str):
        try:
            model = json.loads(model)
        except ValueError as exc:
            return {"ok": False, "message": f"not valid JSON: {exc}"}
    err = validate_remote(model)
    if err:
        return {"ok": False, "message": err}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CUSTOM_REMOTE_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(model, f, indent=1, ensure_ascii=False)
    os.replace(tmp, CUSTOM_REMOTE_FILE)
    LEARNED_FILE.unlink(missing_ok=True)   # codes learned on another remote say nothing about this one
    return {"ok": True, "message": f'saved: {len(model["buttons"])} buttons'}


def get_remote_prompt(_q=None):
    try:
        return {"ok": True, "text": PROMPT_FILE.read_text(encoding="utf8")}
    except OSError:
        return {"ok": False, "message": "remote-prompt.md is missing"}


def get_remote(_q=None):
    return remote_model()


def do_remote(data):
    """Learn the code of a button ({"id", "code"}) or go back to the default ({"id", "reset": true})."""
    bid, code = data.get("id"), data.get("code")
    if bid not in remote_model()["buttons"]:
        return {"ok": False, "message": "unknown button"}
    learned = _read_learned()
    if data.get("reset") is True:
        learned.pop(bid, None)
    elif isinstance(code, str) and core.KEY_NAME.match(code):
        for other in [k for k, v in learned.items() if v == code and k != bid]:
            del learned[other]          # one code belongs to one button
        learned[bid] = code
    else:
        return {"ok": False, "message": "invalid key code"}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LEARNED_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(learned, f, indent=2)
    os.replace(tmp, LEARNED_FILE)
    return {"ok": True, "message": "saved"}


def get_keymap(_q=None):
    defaults = core.default_keymap()
    source, keys = "defaults", defaults
    try:
        keys = json.loads(KEYMAP_FILE.read_text())["keys"]
        source = "file"
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {"ok": True, "source": source, "keys": keys, "defaults": defaults,
            "key_names": key_names()}


def do_keymap(data):
    raw = data.get("keys")
    if not isinstance(raw, dict) or len(raw) > 200:
        return {"ok": False, "message": "invalid mapping"}
    entries, errors = core.normalise(raw)
    for key, spec in entries.items():
        for sub in [spec] + [s for _, s in spec.get("apps", [])]:
            for slot in ("tap", "long"):
                act = sub[slot]
                if act and act["type"] == "app" and core.desktop_path(act["id"]) is None:
                    errors.append(f"{key}: application {act['id']} is not installed")
    if errors:
        return {"ok": False, "message": "; ".join(errors[:5])}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = KEYMAP_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"version": 1, "keys": to_file_format(entries)}, f, indent=2)
    os.replace(tmp, KEYMAP_FILE)
    return {"ok": True, "message": f"saved {len(entries)} keys (applied within a second)"}


def do_dryrun(data):
    """Dry run on: the receiver only logs what it would press or launch."""
    on = data.get("on")
    if not isinstance(on, bool):
        return {"ok": False, "message": "invalid value"}
    try:
        if on:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            core.DRYRUN_FILE.touch()
        else:
            core.DRYRUN_FILE.unlink(missing_ok=True)
    except OSError as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "message": "dry run " + ("ON: nothing is applied" if on else "OFF: keys are applied")}


def do_pointer(data):
    """hide=True: the mouse pointer is moved out of the way on remote presses (default)."""
    hide = data.get("hide")
    if not isinstance(hide, bool):
        return {"ok": False, "message": "invalid value"}
    try:
        if hide:
            core.POINTER_FILE.unlink(missing_ok=True)
        else:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            core.POINTER_FILE.touch()
    except OSError as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "message": "mouse pointer will be " + ("hidden on remote presses" if hide else "left alone")}


def do_keymap_reset(_data):
    try:
        KEYMAP_FILE.unlink()
    except FileNotFoundError:
        pass
    return {"ok": True, "message": "back to the built-in defaults"}


def recent(_q=None):
    """Remote keys the receiver saw in the last 90 s, newest first."""
    _, journal = run(["journalctl", "--user", "-u", SERVICE, "--since", "-90s", "-o", "cat",
                      "--no-pager"], 5)
    seen = []
    for line in reversed(journal.splitlines()):
        m = SEEN.match(line.strip())
        if m and m.group(1) not in seen:
            seen.append(m.group(1))
    return {"ok": True, "keys": seen[:12]}


POST = {"/api/pair": do_pair, "/api/connect": do_connect, "/api/service": do_service,
        "/api/keymap": do_keymap, "/api/keymap/reset": do_keymap_reset, "/api/dryrun": do_dryrun,
        "/api/pointer": do_pointer, "/api/remote": do_remote,
        "/api/remote/model": do_remote_model}
GET = {"/api/status": status, "/api/discover": discover, "/api/keymap": get_keymap,
       "/api/recent": recent, "/api/apps": list_apps, "/api/events": events,
       "/api/profiles": profiles, "/api/monitor": monitor, "/api/remote": get_remote,
       "/api/remote/prompt": get_remote_prompt}


class Handler(BaseHTTPRequestHandler):
    token = ""
    server_version = "cec-over-lan"

    def log_message(self, fmt, *args):  # no tokens/codes in logs
        print(f"{self.client_address[0]} {self.command} {urlparse(self.path).path}", flush=True)

    def _allowed(self):
        try:
            return ipaddress.ip_address(self.client_address[0]).is_private
        except ValueError:
            return False

    def _authed(self):
        given = self.headers.get("X-Token", "")
        return hmac.compare_digest(given.encode(), self.token.encode())

    def _send(self, code, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if not self._allowed():
            return self._send(403, {"ok": False, "message": "forbidden"})
        path = urlparse(self.path).path
        if path == "/":
            return self._send(200, (HERE / "web" / "index.html").read_bytes(),
                              "text/html; charset=utf-8")
        if path in GET:
            if not self._authed():
                return self._send(401, {"ok": False, "message": "bad token"})
            return self._send(200, GET[path](parse_qs(urlparse(self.path).query)))
        self._send(404, {"ok": False, "message": "not found"})

    def do_POST(self):
        if not self._allowed():
            return self._send(403, {"ok": False, "message": "forbidden"})
        handler = POST.get(urlparse(self.path).path)
        if not handler:
            return self._send(404, {"ok": False, "message": "not found"})
        if not self._authed():
            return self._send(401, {"ok": False, "message": "bad token"})
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 65536)
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            return self._send(400, {"ok": False, "message": "bad request"})
        self._send(200, handler(data))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--bind", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()
    Handler.token = load_token()
    print(f"listening on {args.bind}:{args.port}; token file: {TOKEN_FILE}", flush=True)
    ThreadingHTTPServer((args.bind, args.port), Handler).serve_forever()
