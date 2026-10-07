"""Shared fixtures.

Everything runs against the JSON fixture backend, so the suite needs neither
the game, the parser, nor systemd.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from paleditor import auth, catalog, db
from paleditor.config import Config, from_dict

FIXTURE_WORLD = Path(__file__).parent / "fixtures" / "world.json"

FRIEND_PASSWORD = "friend-pw"
OWNER_PASSWORD = "owner-pw"


@pytest.fixture(scope="session")
def friend_hash() -> str:
    return auth.hash_password(FRIEND_PASSWORD)


@pytest.fixture(scope="session")
def owner_hash() -> str:
    return auth.hash_password(OWNER_PASSWORD)


@pytest.fixture
def save_dir(tmp_path: Path) -> Path:
    """A save directory whose Level.sav is really the JSON fixture."""
    target = tmp_path / "save"
    target.mkdir()
    shutil.copy(FIXTURE_WORLD, target / "Level.sav")
    return target


@pytest.fixture
def config(tmp_path: Path, save_dir: Path, friend_hash: str, owner_hash: str) -> Config:
    return from_dict(
        {
            "server": {"listen": ["127.0.0.1:8080"]},
            "auth": {
                "password_hash": friend_hash,
                "owner_password_hash": owner_hash,
            },
            "palworld": {
                "save_dir": str(save_dir),
                "save_backend": "fixture",
                "server_unit": "palworld-test.service",
            },
            "maintenance": {
                "backup_dir": str(tmp_path / "backups"),
                "backup_count": 3,
                "shutdown_warning_seconds": 0,
                "shutdown_timeout_seconds": 10,
                "lock_file": str(tmp_path / "window.lock"),
            },
            "database": {"path": str(tmp_path / "paleditor.db")},
        }
    )


@pytest.fixture
def conn(config: Config) -> sqlite3.Connection:
    db.init(config.database.path)
    connection = db.connect(config.database.path)
    catalog.seed(connection)
    yield connection
    connection.close()


@pytest.fixture
def ingested(config: Config, conn: sqlite3.Connection):
    """A database with one successful ingest in it."""
    from paleditor import ingest

    result = ingest.run(config, conn=conn)
    return result


@pytest.fixture
def client(config: Config, ingested):
    from fastapi.testclient import TestClient

    from paleditor.app import create_app

    app = create_app(config, start_scheduler=False)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def friend_client(client):
    response = client.post("/api/session", json={"password": FRIEND_PASSWORD})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def owner_client(client):
    response = client.post("/api/session", json={"password": OWNER_PASSWORD})
    assert response.status_code == 200, response.text
    return client


class FakeServerControl:
    """Stands in for systemd. Records the calls the window makes."""

    def __init__(
        self,
        *,
        running: bool = True,
        start_fails: bool = False,
        exists: bool = True,
    ):
        self.running = running
        self.start_fails = start_fails
        self.exists = exists
        self.calls: list[str] = []
        self.pid: int | None = None

    def unit_exists(self) -> bool:
        return self.exists

    def is_running(self) -> bool:
        return self.running

    def main_pid(self) -> int | None:
        return self.pid

    def start(self) -> None:
        self.calls.append("start")
        if self.start_fails:
            raise RuntimeError("systemctl start failed")
        self.running = True

    def stop(self) -> None:
        self.calls.append("stop")
        self.running = False


class FakeRcon:
    def __init__(self, recorder: list[str] | None = None, fail: bool = False):
        self.recorder = recorder if recorder is not None else []
        self.fail = fail

    def __enter__(self):
        if self.fail:
            from paleditor.errors import RconError

            raise RconError("connection refused")
        return self

    def __exit__(self, *exc_info):
        return None

    def save(self):
        self.recorder.append("save")
        return "Complete Save"

    def shutdown(self, seconds: int, message: str):
        self.recorder.append(f"shutdown {seconds} {message}")
        return "Shutdown"

    def info(self):
        return "Welcome to Pal Server"


@pytest.fixture
def fake_control() -> FakeServerControl:
    return FakeServerControl()


def read_world(save_dir: Path) -> dict:
    return json.loads((save_dir / "Level.sav").read_text())


def slot_of(world: dict, container_guid: str, slot_index: int) -> dict | None:
    """One stored slot, or None when nothing occupies that index.

    The save records only occupied slots, so an empty slot is an absent entry
    rather than a zeroed one.
    """
    slots = world["containers"][container_guid]["slots"]
    return next((s for s in slots if s["slot_index"] == slot_index), None)
