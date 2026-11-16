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
            "dry_run": core.DRYRUN_FILE.exists(), "would": would[-10:]}


def discover(_q=None):
    """Best effort: read adb's mDNS list (needs the Wireless debugging screens open on the TV)."""
    _, out = run(["adb", "mdns", "services"], 8)
    found = {"host": "", "pair_port": 0, "connect_port": 0}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 3 or ":" not in parts[-1]:
            continue
        host, _, port = parts[-1].rpartition(":")
        if not (valid_host(host) and port.isdigit()):
            continue
        if "_adb-tls-pairing" in line:
            found["host"], found["pair_port"] = host, int(port)
        elif "_adb-tls-connect" in line:
            found["host"] = found["host"] or host
            found["connect_port"] = int(port)
    ok = bool(found["host"])
    return {"ok": ok, "found": found,
            "message": "ports filled in" if ok else "nothing discovered (enter the values by hand)"}


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


def to_file_format(entries):
    out = {}
    for key, spec in entries.items():
        item = {}
        if spec["tap"]:
            item["tap"] = core.action_to_text(spec["tap"])
        if spec["long"]:
            item["long"] = core.action_to_text(spec["long"])
            item["hold"] = spec["hold"]
        out[key] = item
    return out


def parse_desktop(path):
    """Name of a launchable application from a .desktop file, or None."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    name, kind, in_entry = None, None, False
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
        elif k == "Type":
            kind = v
        elif k in ("NoDisplay", "Hidden") and v.strip().lower() == "true":
            return None
    if kind != "Application" or not name:
        return None
    return {"id": path.name, "name": name}


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
            "held": float(f["held"]) if "held" in f else None, "result": f.get("result")})
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
        for slot in ("tap", "long"):
            act = spec[slot]
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
        "/api/keymap": do_keymap, "/api/keymap/reset": do_keymap_reset, "/api/dryrun": do_dryrun}
GET = {"/api/status": status, "/api/discover": discover, "/api/keymap": get_keymap,
       "/api/recent": recent, "/api/apps": list_apps, "/api/events": events,
       "/api/profiles": profiles, "/api/monitor": monitor}


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
