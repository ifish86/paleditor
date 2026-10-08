"""The hand-written RCON client, against a socket that speaks the protocol."""

from __future__ import annotations

import socket
import struct
import threading

import pytest

from paleditor.errors import RconError
from paleditor.rcon import RconClient, wait_until_responsive

PASSWORD = "secret"


def _pack(request_id: int, packet_type: int, body: str) -> bytes:
    payload = struct.pack("<ii", request_id, packet_type) + body.encode() + b"\x00\x00"
    return struct.pack("<i", len(payload)) + payload


def _read_packet(conn: socket.socket) -> tuple[int, int, str]:
    size = struct.unpack("<i", _read_exact(conn, 4))[0]
    payload = _read_exact(conn, size)
    request_id, packet_type = struct.unpack("<ii", payload[:8])
    return request_id, packet_type, payload[8:].rstrip(b"\x00").decode()


def _read_exact(conn: socket.socket, length: int) -> bytes:
    chunks = []
    while length > 0:
        chunk = conn.recv(length)
        if not chunk:
            raise ConnectionError("closed")
        chunks.append(chunk)
        length -= len(chunk)
    return b"".join(chunks)


class FakeRconServer:
    """A one-connection Source RCON server."""

    def __init__(self, *, password: str = PASSWORD, reject_auth: bool = False):
        self.password = password
        self.reject_auth = reject_auth
        self.commands: list[str] = []
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc_info):
        self._sock.close()

    def _serve(self) -> None:
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        with conn:
            try:
                request_id, _, body = _read_packet(conn)
                if self.reject_auth or body != self.password:
                    conn.sendall(_pack(-1, 2, ""))
                    return
                # Many servers send an empty value packet before the auth reply.
                conn.sendall(_pack(request_id, 0, ""))
                conn.sendall(_pack(request_id, 2, ""))
                while True:
                    request_id, _, body = _read_packet(conn)
                    self.commands.append(body)
                    conn.sendall(_pack(request_id, 0, f"ok: {body}"))
            except (ConnectionError, OSError, struct.error):
                return


def test_authenticates_and_runs_a_command():
    with FakeRconServer() as server:
        with RconClient("127.0.0.1", server.port, PASSWORD, timeout=5.0) as client:
            assert client.command("Info") == "ok: Info"
        assert server.commands == ["Info"]


def test_save_and_shutdown_send_the_expected_commands():
    with FakeRconServer() as server:
        with RconClient("127.0.0.1", server.port, PASSWORD, timeout=5.0) as client:
            client.save()
            client.shutdown(60, "paleditor maintenance")
        # Spaces become underscores: some builds reject a message containing one.
        assert server.commands == ["Save", "Shutdown 60 paleditor_maintenance"]


def test_a_wrong_password_is_reported_clearly():
    with FakeRconServer() as server:
        with pytest.raises(RconError, match="wrong password"):
            RconClient("127.0.0.1", server.port, "not-it", timeout=5.0).connect()


def test_a_refused_connection_is_reported_clearly():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # nothing is listening now
    with pytest.raises(RconError, match="cannot connect"):
        RconClient("127.0.0.1", port, PASSWORD, timeout=2.0).connect()


def test_wait_until_responsive_succeeds_against_a_live_server():
    with FakeRconServer() as server:
        assert wait_until_responsive(
            "127.0.0.1", server.port, PASSWORD, timeout=5.0, interval=0.1
        ) is True


def test_wait_until_responsive_gives_up_on_a_dead_server():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    assert wait_until_responsive(
        "127.0.0.1", port, PASSWORD, timeout=1.0, interval=0.2
    ) is False


# -- Palworld's loose request ids ------------------------------------------


class IdZeroServer(FakeRconServer):
    """Answers every packet with id 0, the way Palworld does."""

    def _serve(self) -> None:
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        with conn:
            try:
                _read_packet(conn)
                conn.sendall(_pack(0, 2, ""))      # auth ok, id 0 not the sent id
                while True:
                    _, _, body = _read_packet(conn)
                    self.commands.append(body)
                    conn.sendall(_pack(0, 0, f"ok: {body}"))
            except (ConnectionError, OSError, struct.error):
                return


def test_a_server_that_does_not_echo_request_ids_still_works():
    """Palworld answers with id 0 regardless of what was sent.

    Treating that as an error meant every graceful shutdown fell back to
    killing the unit, and the post-restart RCON check could never succeed - so
    every window spent its full RCON timeout waiting for nothing.
    """
    with IdZeroServer() as server:
        with RconClient("127.0.0.1", server.port, PASSWORD, timeout=5.0) as client:
            assert client.save() == "ok: Save"
            client.shutdown(60, "paleditor maintenance")
        assert server.commands == ["Save", "Shutdown 60 paleditor_maintenance"]


def test_a_wrong_password_is_still_rejected():
    """Loosening the id check must not loosen authentication: -1 is the
    protocol's way of saying the password was wrong."""
    with FakeRconServer(reject_auth=True) as server:
        with pytest.raises(RconError, match="wrong password"):
            RconClient("127.0.0.1", server.port, PASSWORD, timeout=5.0).connect()
