"""Minimal Source RCON client, enough for Factorio.

Protocol: https://developer.valvesoftware.com/wiki/Source_RCON_Protocol
Factorio answers each command with a single packet however large (tested to
3 MB both ways on 2.1.22), so no multi-packet reassembly is needed.
"""

import socket
import struct

AUTH, EXEC, RESPONSE = 3, 2, 0
AUTH_RESPONSE = 2  # same value as EXEC; the direction tells them apart


class RconError(RuntimeError):
    pass


class Rcon:
    def __init__(self, host: str, port: int, password: str, timeout: float = 120):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self._id = 0
        self._send(AUTH, password)
        while True:  # some servers send an empty RESPONSE before AUTH_RESPONSE
            response_id, kind, _ = self._recv()
            if kind == AUTH_RESPONSE:
                break
        if response_id == -1:
            raise RconError("RCON authentication failed")

    def command(self, text: str) -> str:
        request_id = self._send(EXEC, text)
        while True:
            response_id, kind, body = self._recv()
            if kind == RESPONSE and response_id == request_id:
                return body

    def close(self):
        self.sock.close()

    def _send(self, kind: int, body: str) -> int:
        self._id += 1
        payload = body.encode() + b"\0\0"
        self.sock.sendall(struct.pack("<iii", len(payload) + 8, self._id, kind) + payload)
        return self._id

    def _recv(self) -> tuple[int, int, str]:
        (size,) = struct.unpack("<i", self._read(4))
        data = self._read(size)
        response_id, kind = struct.unpack("<ii", data[:8])
        return response_id, kind, data[8:-2].decode()

    def _read(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RconError("connection closed by server")
            buf += chunk
        return buf
