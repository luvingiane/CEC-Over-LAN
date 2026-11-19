"""Receives the remote's keys from the CEC-Over-LAN app on the TV (the app is in android/).

The app connects to this machine over TCP and sends text lines, one per event:

    HELLO <token>        first line; the token is the one in ~/cec-over-lan/token. Answered with OK.
    KEY_RED DOWN         a remote key went down or up (the Linux name, as `adb shell getevent` shows it)
    KEY_RED UP
    GATE yes|no          whether the TV is showing this machine (the app reads it on the TV)
    PING                 every few seconds; silence for SILENCE_S closes the connection
    WIN <text>           which window came to the front on the TV; only written to the log (it tells
                         whether the TV changed input, see the gate)

Only private addresses are served, the token is compared in constant time, lines are short and the
format is strict, so a stray client can neither press a key it should not nor fill the memory.
The module does not import the receiver: it calls the functions it is given.
"""
import hmac
import ipaddress
import re
import socket
import threading

KEY_LINE = re.compile(r"^(KEY_[A-Z0-9_]{1,40}) (DOWN|UP)$")
GATE_LINE = re.compile(r"^GATE (yes|no)$")
MAX_LINE = 200
HELLO_TIMEOUT_S = 5.0
SILENCE_S = 15.0       # the app sends PING every 5 s
MAX_BAD_LINES = 20


def hang_up(conn):
    """Close for real: a socket with a makefile() reader stays open after close(), shutdown() ends it."""
    try:
        conn.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        conn.close()
    except OSError:
        pass


def is_private(addr):
    try:
        return ipaddress.ip_address(addr).is_private
    except ValueError:
        return False


class AppSource:
    def __init__(self, token, on_key, port=8766, bind="0.0.0.0", on_connect=None,
                 on_disconnect=None, log=print, silence_s=SILENCE_S):
        self.token = token.encode() if isinstance(token, str) else token
        self.on_key, self.on_connect, self.on_disconnect = on_key, on_connect, on_disconnect
        self.port, self.bind, self.log, self.silence_s = port, bind, log, silence_s
        self._lock = threading.Lock()
        self._client = None     # the connection that is being served
        self._gate = None       # last GATE answer of that client, or None
        self._server = None

    # ------------------------------------------------------------------ state
    @property
    def connected(self):
        return self._client is not None

    def gate_state(self):
        """True/False as the TV app reported it, None if no app is connected or it did not say."""
        return self._gate if self._client is not None else None

    # ------------------------------------------------------------------ server
    def start(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.bind, self.port))
        srv.listen(4)
        self._server = srv
        self.port = srv.getsockname()[1]      # useful when the port was 0 (tests)
        threading.Thread(target=self._accept_loop, daemon=True).start()
        return self.port

    def stop(self):
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
        with self._lock:
            client = self._client
        if client is not None:
            hang_up(client)

    def _accept_loop(self):
        while True:
            try:
                conn, addr = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn, addr[0]), daemon=True).start()

    # ------------------------------------------------------------------ one connection
    def _serve(self, conn, peer):
        if not is_private(peer):
            conn.close()
            return
        reader = conn.makefile("rb")
        try:
            conn.settimeout(HELLO_TIMEOUT_S)
            first = reader.readline(MAX_LINE + 1).decode("ascii", "ignore").strip()
            word, _, given = first.partition(" ")
            if word != "HELLO" or not hmac.compare_digest(given.encode(), self.token):
                self.log(f"[app] refused a connection from {peer}: bad hello")
                return
            conn.sendall(b"OK\n")
            with self._lock:
                old, self._client, self._gate = self._client, conn, None
            if old is not None:
                hang_up(old)             # one app at a time: the newest wins
            self.log(f"[app] TV app connected from {peer}")
            if self.on_connect:
                self.on_connect()
            self._read(conn, reader)
        except (OSError, ValueError):
            pass
        finally:
            with self._lock:
                mine = self._client is conn
                if mine:
                    self._client, self._gate = None, None
            try:
                conn.close()
            except OSError:
                pass
            if mine:
                self.log("[app] TV app disconnected")
                if self.on_disconnect:
                    self.on_disconnect()

    def _read(self, conn, reader):
        conn.settimeout(self.silence_s)
        bad = 0
        while True:
            raw = reader.readline(MAX_LINE + 1)
            if not raw:
                return
            line = raw.decode("ascii", "ignore").strip()
            m = KEY_LINE.match(line)
            if m:
                self.on_key(m.group(1), m.group(2))
                continue
            g = GATE_LINE.match(line)
            if g:
                self._gate = g.group(1) == "yes"
                continue
            if line == "PING":
                continue
            if line.startswith("WIN "):
                self.log("[window] " + line[4:150])
                continue
            bad += 1
            if bad > MAX_BAD_LINES:
                return
