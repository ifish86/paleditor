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
    if readonly:
        conn = sqlite3.connect(
            f"file:{path}?mode=ro", uri=True, timeout=5.0,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not readonly:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init(path: str | Path) -> None:
    """Create the schema if it is not already there. Safe to run repeatedly."""
    with closing_connect(path) as conn:
        conn.executescript(schema_sql())
        conn.commit()


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
