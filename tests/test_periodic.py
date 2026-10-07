"""Periodic ingest and multi-address binding.

Both exist because of what a running deployment showed: the service came up,
served one of its two configured addresses, and would never have refreshed its
data again. The maintenance window reingests only as its verification step, and
it returns early when the queue is empty, which is most nights.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest

from paleditor import ingest
from paleditor.config import IngestConfig, ListenAddress, from_dict
from paleditor.errors import ConfigError
from paleditor.scheduler import IntervalWorker


# -- only reread when the world moved --------------------------------------


def test_the_first_call_ingests(config, conn):
    assert ingest.run_if_changed(config, conn=conn) is not None


def test_an_unchanged_save_is_skipped(config, conn):
    first = ingest.run_if_changed(config, conn=conn)
    assert first is not None
    assert ingest.run_if_changed(config, conn=conn) is None, (
        "parsing an identical save again is wasted work"
    )


def test_a_changed_save_is_reingested(config, conn, save_dir: Path):
    first = ingest.run_if_changed(config, conn=conn)
    world = json.loads((save_dir / "Level.sav").read_text())
    world["objects"][0]["lock_code"] = "9999"
    (save_dir / "Level.sav").write_text(json.dumps(world))

    second = ingest.run_if_changed(config, conn=conn)
    assert second is not None
    assert second.rev > first.rev
    code = conn.execute(
        "SELECT lock_code FROM chests WHERE lock_code = '9999'"
    ).fetchone()
    assert code is not None


def test_a_failed_parse_does_not_count_as_unchanged(config, conn, save_dir: Path):
    """A broken save must not be remembered as the current state, or the next
    good one would be skipped."""
    from paleditor.errors import SaveFormatError

    ingest.run_if_changed(config, conn=conn)
    (save_dir / "Level.sav").write_text("not json")
    with pytest.raises(SaveFormatError):
        ingest.run_if_changed(config, conn=conn)


# -- the worker -------------------------------------------------------------


def test_the_worker_runs_immediately_rather_than_after_one_interval():
    """A fresh service should have data without waiting out the first tick."""
    ran = threading.Event()
    worker = IntervalWorker(3600, ran.set, name="test")
    worker.start()
    try:
        assert ran.wait(5), "the worker did not run at startup"
    finally:
        worker.stop()


def test_the_worker_keeps_going_after_a_failing_tick():
    calls = []

    def flaky():
        calls.append(1)
        raise RuntimeError("boom")

    worker = IntervalWorker(0.1, flaky, name="test")
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while len(calls) < 3 and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        worker.stop()
    assert len(calls) >= 3, "a raising tick must not kill the thread"


def test_the_worker_stops():
    worker = IntervalWorker(0.05, lambda: None, name="test")
    worker.start()
    worker.stop()
    assert worker._thread is None


# -- configuration ----------------------------------------------------------


def test_ingest_defaults_to_five_minutes():
    assert IngestConfig().enabled is True
    assert IngestConfig().interval_seconds == 300


def test_too_frequent_an_interval_is_refused(
    friend_hash, owner_hash, save_dir, tmp_path
):
    """The server writes a new snapshot every ~30s; parsing faster is waste."""
    with pytest.raises(ConfigError, match="interval_seconds"):
        from_dict(
            {
                "server": {"listen": ["127.0.0.1:8080"]},
                "auth": {
                    "password_hash": friend_hash,
                    "owner_password_hash": owner_hash,
                },
                "palworld": {"save_dir": str(save_dir), "save_backend": "fixture"},
                "maintenance": {"backup_dir": str(tmp_path / "b")},
                "database": {"path": str(tmp_path / "db" / "p.db")},
                "ingest": {"interval_seconds": 5},
            }
        )


def test_a_disabled_ingest_skips_the_interval_check(
    friend_hash, owner_hash, save_dir, tmp_path
):
    config = from_dict(
        {
            "server": {"listen": ["127.0.0.1:8080"]},
            "auth": {"password_hash": friend_hash, "owner_password_hash": owner_hash},
            "palworld": {"save_dir": str(save_dir), "save_backend": "fixture"},
            "maintenance": {"backup_dir": str(tmp_path / "b")},
            "database": {"path": str(tmp_path / "db" / "p.db")},
            "ingest": {"enabled": False, "interval_seconds": 1},
        }
    )
    assert config.ingest.enabled is False


# -- binding every configured address ---------------------------------------


def test_each_configured_address_gets_its_own_socket():
    """uvicorn takes one host, but accepts pre-bound sockets, so the VPN
    address in the config is actually served rather than noted and ignored."""
    from paleditor.cli import _bind

    sockets = []
    try:
        for host in ("127.0.0.1", "127.0.0.2"):
            sockets.append(_bind(ListenAddress(host=host, port=0)))
        bound = {s.getsockname()[0] for s in sockets}
        assert bound == {"127.0.0.1", "127.0.0.2"}
    finally:
        for s in sockets:
            s.close()


def test_an_ipv6_socket_does_not_also_claim_ipv4():
    from paleditor.cli import _bind

    sock = _bind(ListenAddress(host="::1", port=0))
    try:
        assert sock.family == socket.AF_INET6
        assert sock.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY) == 1
    finally:
        sock.close()


def test_binding_a_taken_port_raises_rather_than_starting_half_served():
    from paleditor.cli import _bind

    first = _bind(ListenAddress(host="127.0.0.1", port=0))
    try:
        port = first.getsockname()[1]
        with pytest.raises(OSError):
            _bind(ListenAddress(host="127.0.0.1", port=port))
    finally:
        first.close()
