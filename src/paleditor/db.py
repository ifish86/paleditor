"""SQLite access. One file, WAL mode, short-lived connections.

Reads are served entirely from these tables, which is what keeps the UI
responsive while a full save parse takes over a minute.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Iterator

SCHEMA_RESOURCE = "schema.sql"


def utcnow() -> str:
    """Timestamps are ISO 8601 UTC strings throughout."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def schema_sql() -> str:
    return resources.files("paleditor").joinpath(SCHEMA_RESOURCE).read_text("utf-8")


def connect(path: str | Path, *, readonly: bool = False) -> sqlite3.Connection:
    path = Path(path)
    # check_same_thread=False because FastAPI runs a sync dependency's setup and
    # the endpoint body on different threadpool threads, so a per-request
    # connection is opened on one thread and used on another. Concurrent
    # requests make that routine; sequential ones hide it entirely. Each
    # connection is still used by exactly one request at a time, and CPython's
    # sqlite3 is built in serialized mode (threadsafety == 3), so this is safe.
    if readonly:
        conn = sqlite3.connect(
            f"file:{path}?mode=ro", uri=True, timeout=5.0,
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=False,
        )
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not readonly:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


# Columns added after the first schema. SQLite cannot add them through
# CREATE TABLE IF NOT EXISTS, so an existing database is migrated here.
_ADDED_COLUMNS = (
    ("chests", "kind", "TEXT NOT NULL DEFAULT 'storage'"),
    ("chests", "lockable", "INTEGER NOT NULL DEFAULT 0"),
    ("chests", "has_container", "INTEGER NOT NULL DEFAULT 1"),
    ("items", "icon", "TEXT"),
)


def icon_dir(database_path: Path) -> Path:
    """Where downloaded item icons live: beside the database.

    The same reasoning as the maintenance lock. It is a directory the
    deployment already has to be able to write, so it needs no extra setting
    and cannot point somewhere the service cannot reach.
    """
    return Path(database_path).parent / "icons"


def init(path: str | Path) -> None:
    """Create the schema if it is not already there. Safe to run repeatedly."""
    with closing_connect(path) as conn:
        conn.executescript(schema_sql())
        _migrate(conn)
        conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, definition in _ADDED_COLUMNS:
        existing = {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
        }
        if not existing:
            continue
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


@contextmanager
def closing_connect(path: str | Path, *, readonly: bool = False) -> Iterator[sqlite3.Connection]:
    conn = connect(path, readonly=readonly)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block in one transaction, rolling back on any exception.

    Used by the queue claim in the maintenance window, where a partial commit
    would let a second worker pick up the same edits.
    """
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_state(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def latest_ingest(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """The most recent successful ingest, which defines the current rev."""
    return conn.execute(
        "SELECT * FROM ingests WHERE status = 'ok' ORDER BY rev DESC LIMIT 1"
    ).fetchone()


def current_rev(conn: sqlite3.Connection) -> int | None:
    row = latest_ingest(conn)
    return row["rev"] if row else None
