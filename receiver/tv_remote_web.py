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
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
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


def status():
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
    return {"target": target, "service": active, "link": link, "on_screen": on_screen}


def discover():
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


POST = {"/api/pair": do_pair, "/api/connect": do_connect, "/api/service": do_service}


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
        if path in ("/api/status", "/api/discover"):
            if not self._authed():
                return self._send(401, {"ok": False, "message": "bad token"})
            return self._send(200, status() if path == "/api/status" else discover())
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
            length = min(int(self.headers.get("Content-Length", "0")), 4096)
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
