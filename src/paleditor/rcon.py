"""A minimal Source RCON client.

Palworld speaks the Source RCON protocol. Only three commands matter here:
Save, Shutdown and Info, so this is a short implementation rather than a
dependency.
"""

from __future__ import annotations

import logging
import socket
import struct

from .errors import RconError

SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_RESPONSE_VALUE = 0

log = logging.getLogger(__name__)


class RconClient:
    def __init__(self, host: str, port: int, password: str, *, timeout: float = 10.0):
        self.host = host
        self.port = port
        self._password = password
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._request_id = 0

    # -- plumbing ----------------------------------------------------------

    def __enter__(self) -> "RconClient":
        self.connect()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def connect(self) -> None:
        try:
            self._sock = socket.create_connection((self.host, self.port), self.timeout)
            self._sock.settimeout(self.timeout)
        except OSError as exc:
            raise RconError(
                f"cannot connect to RCON at {self.host}:{self.port}: {exc}"
            ) from exc
        self._authenticate()

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _send(self, packet_type: int, body: str) -> int:
        if self._sock is None:
            raise RconError("RCON socket is not connected")
        request_id = self._next_id()
        payload = body.encode("utf-8") + b"\x00\x00"
        packet = struct.pack("<ii", request_id, packet_type) + payload
        try:
            self._sock.sendall(struct.pack("<i", len(packet)) + packet)
        except OSError as exc:
            raise RconError(f"RCON send failed: {exc}") from exc
        return request_id

    def _recv_exact(self, length: int) -> bytes:
        assert self._sock is not None
        chunks = []
        remaining = length
        while remaining > 0:
            try:
                chunk = self._sock.recv(remaining)
            except socket.timeout as exc:
                raise RconError("RCON read timed out") from exc
            except OSError as exc:
                raise RconError(f"RCON read failed: {exc}") from exc
            if not chunk:
                raise RconError("RCON connection closed mid-packet")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _recv(self) -> tuple[int, int, str]:
        size = struct.unpack("<i", self._recv_exact(4))[0]
        if not 10 <= size <= 4106:
            raise RconError(f"RCON returned an implausible packet size: {size}")
        payload = self._recv_exact(size)
        request_id, packet_type = struct.unpack("<ii", payload[:8])
        body = payload[8:].rstrip(b"\x00").decode("utf-8", errors="replace")
        return request_id, packet_type, body

    def _authenticate(self) -> None:
        sent_id = self._send(SERVERDATA_AUTH, self._password)
        # Some servers send an empty RESPONSE_VALUE before the auth response.
        for _ in range(3):
            request_id, packet_type, _ = self._recv()
            if packet_type == SERVERDATA_AUTH_RESPONSE:
                if request_id == -1:
                    raise RconError("RCON authentication failed: wrong password")
                if request_id != sent_id:
                    raise RconError("RCON authentication returned a mismatched id")
                return
        raise RconError("RCON authentication did not return a response")

    # -- commands ----------------------------------------------------------

    def command(self, body: str) -> str:
        sent_id = self._send(SERVERDATA_EXECCOMMAND, body)
        request_id, _, response = self._recv()
        if request_id != sent_id:
            raise RconError(f"RCON response id {request_id} did not match {sent_id}")
        return response

    def save(self) -> str:
        return self.command("Save")

    def shutdown(self, seconds: int, message: str) -> str:
        # Palworld's Shutdown takes the message without spaces in some builds;
        # underscores are the convention the community settled on.
        safe = message.replace(" ", "_")
        return self.command(f"Shutdown {seconds} {safe}")

    def info(self) -> str:
        return self.command("Info")


def wait_until_responsive(
    host: str, port: int, password: str, *, timeout: float, interval: float = 3.0
) -> bool:
    """Poll RCON until it answers Info, or give up.

    Used after starting the server so the window does not report success
    before the world is actually accepting players.
    """
    import time

    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with RconClient(host, port, password, timeout=5.0) as client:
                client.info()
                return True
        except RconError as exc:
            last_error = exc
            time.sleep(interval)
    log.warning("RCON never became responsive within %ss: %s", timeout, last_error)
    return False
