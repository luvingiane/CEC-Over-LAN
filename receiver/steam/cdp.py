#!/usr/bin/env python3
"""A tiny Chrome DevTools Protocol client using only the standard library.

Steam's web UI (CEF) exposes the DevTools protocol on 127.0.0.1:8080 when Steam is started with
`-cef-enable-debugging`. This module lists the pages and evaluates JavaScript in one of them.
It talks to localhost only.
"""
import base64
import hashlib
import json
import os
import socket
import struct
import urllib.request

HOST, PORT = "127.0.0.1", 8080


def list_pages(host=HOST, port=PORT, timeout=3):
    with urllib.request.urlopen(f"http://{host}:{port}/json/list", timeout=timeout) as r:
        return json.load(r)


def find_page(title_contains, pages=None):
    for page in pages if pages is not None else list_pages():
        if title_contains.lower() in (page.get("title") or "").lower() and page.get("webSocketDebuggerUrl"):
            return page
    return None


class CDP:
    """One WebSocket connection to one page."""

    def __init__(self, ws_url, timeout=10):
        assert ws_url.startswith("ws://127.0.0.1:") or ws_url.startswith("ws://localhost:"), "localhost only"
        rest = ws_url[len("ws://"):]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("websocket handshake failed")
            head += chunk
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if b"101" not in head.split(b"\r\n")[0] or accept not in head:
            raise ConnectionError("websocket handshake refused")
        self._buf = head.split(b"\r\n\r\n", 1)[1]
        self._id = 0

    def _read(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("connection closed")
            self._buf += chunk
        data, self._buf = self._buf[:n], self._buf[n:]
        return data

    def _send(self, text):
        payload = text.encode()
        header = bytearray([0x81])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(bytes(header) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def _recv(self):
        message = b""
        while True:
            b1, b2 = self._read(2)
            fin, opcode, length = b1 & 0x80, b1 & 0x0F, b2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            data = self._read(length)
            if opcode == 0x8:
                raise ConnectionError("closed by the page")
            if opcode in (0x1, 0x0):
                message += data
                if fin:
                    return message.decode()

    def call(self, method, **params):
        self._id += 1
        self._send(json.dumps({"id": self._id, "method": method, "params": params}))
        while True:
            reply = json.loads(self._recv())
            if reply.get("id") == self._id:
                if "error" in reply:
                    raise RuntimeError(reply["error"])
                return reply.get("result", {})

    def evaluate(self, expression, await_promise=False):
        """Run JavaScript in the page and return its value (JSON-serialisable values only)."""
        result = self.call("Runtime.evaluate", expression=expression, returnByValue=True,
                           awaitPromise=await_promise)
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"].get("exception", {}).get("description", "JS exception"))
        return result["result"].get("value")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
