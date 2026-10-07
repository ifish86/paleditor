"""The ingest worker: Level.sav in, SQLite rows out.

Every pass opens a new ``rev``. Ingest-owned rows are upserted with that rev,
and a chest that stops appearing keeps its old ``last_seen_rev`` so the UI can
show it as orphaned instead of having it vanish when someone dismantles it.

``chest_meta`` and ``pending_edits`` are never written here. That is what makes
nicknames survive a reparse.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from . import catalog, db
from .config import Config
from .errors import ParserUnavailable, SaveFormatError
from . import savesource
from .saves import WorldSnapshot, get_backend

log = logging.getLogger(__name__)

# A save that parses but yields nothing is treated as a format failure rather
# than a world where every chest was dismantled at once.
MIN_PLAUSIBLE_CHESTS = 1


def _backend_for(config: Config):
    """Build the configured backend, passing through the Oodle library path."""
    if config.palworld.save_backend == "palworld":
        return get_backend("palworld", oodle_library=config.palworld.oodle_library)
    return get_backend(config.palworld.save_backend)


@dataclass
class IngestResult:
    rev: int
    chest_count: int
    base_count: int
    duration_ms: int
    lock_codes_available: bool
    warnings: tuple[str, ...]
    orphaned: int


def run(config: Config, *, conn: sqlite3.Connection | None = None) -> IngestResult:
    """Parse the save and write a new rev. Raises on any format mismatch."""
    own_conn = conn is None
    if own_conn:
        db.init(config.database.path)
        conn = db.connect(config.database.path)
    try:
        return _run(config, conn)
    finally:
        if own_conn:
            conn.close()


def _run(config: Config, conn: sqlite3.Connection) -> IngestResult:
    backend = _backend_for(config)
    # Prefer a completed snapshot. Reading the live file while the server is
    # writing it returns a torn save, which is the single easiest way to ingest
    # a world that never existed.
    source = savesource.pick(
        config.palworld.save_dir, prefer_backup=config.palworld.read_from_backup
    )
    level = source.level_sav
    started = time.monotonic()
    log.info("ingesting from %s (%s)", level, source.label)

    cursor = conn.execute(
        "INSERT INTO ingests(started_at, status, backend) VALUES (?, 'running', ?)",
        (db.utcnow(), backend.name),
    )
    rev = int(cursor.lastrowid)
    conn.commit()

    try:
        snapshot = backend.read_snapshot(level)
        _validate(snapshot)
    except (SaveFormatError, ParserUnavailable) as exc:
        conn.execute(
            "UPDATE ingests SET status='failed', finished_at=?, error=? WHERE rev=?",
            (db.utcnow(), str(exc), rev),
        )
        conn.commit()
        log.error("ingest rev %s failed: %s", rev, exc)
        raise

    with db.transaction(conn):
        _write_bases(conn, snapshot, rev)
        _write_chests(conn, snapshot, rev)
        duration_ms = int((time.monotonic() - started) * 1000)
        conn.execute(
            "UPDATE ingests SET status='ok', finished_at=?, save_mtime=?, "
            "save_sha256=?, chest_count=?, duration_ms=? WHERE rev=?",
            (
                db.utcnow(),
                snapshot.save_mtime,
                snapshot.save_sha256,
                len(snapshot.chests),
                duration_ms,
                rev,
            ),
        )

    orphaned = conn.execute(
        "SELECT COUNT(*) FROM chests WHERE last_seen_rev < ?", (rev,)
    ).fetchone()[0]

    catalog.seed(conn)
    catalog.record_observed(conn)

    log.info(
        "ingest rev %s ok: %s chests, %s bases, %sms%s",
        rev, len(snapshot.chests), len(snapshot.bases), duration_ms,
        "" if snapshot.lock_codes_available else " (contents-only, no lock codes)",
    )
    return IngestResult(
        rev=rev,
        chest_count=len(snapshot.chests),
        base_count=len(snapshot.bases),
        duration_ms=duration_ms,
        lock_codes_available=snapshot.lock_codes_available,
        warnings=snapshot.warnings,
        orphaned=int(orphaned),
    )


def _validate(snapshot: WorldSnapshot) -> None:
    """Refuse a snapshot that does not look like this world.

    Ingest validating its own expectations is the first line of defence against
    a game update: the app shows stale data with a warning rather than
    replacing a good rev with an empty one.
    """
    if len(snapshot.chests) < MIN_PLAUSIBLE_CHESTS:
        raise SaveFormatError(
            "the save parsed but contained no chests at all. Either the world "
            "really is empty or the object layout changed; refusing to replace "
            "the last good rev with an empty one."
        )
    if not snapshot.bases:
        raise SaveFormatError(
            "the save parsed but contained no base camps, which paleditor needs "
            "in order to group chests. Refusing to ingest."
        )


def _write_bases(conn: sqlite3.Connection, snapshot: WorldSnapshot, rev: int) -> None:
    conn.executemany(
        """
        INSERT INTO bases(base_guid, name, x, y, z, guild_id, last_seen_rev)
        VALUES (:guid, :name, :x, :y, :z, :guild, :rev)
        ON CONFLICT(base_guid) DO UPDATE SET
            name          = excluded.name,
            x = excluded.x, y = excluded.y, z = excluded.z,
            guild_id      = excluded.guild_id,
            last_seen_rev = excluded.last_seen_rev
        """,
        [
            {
                "guid": b.base_guid, "name": b.name,
                "x": b.x, "y": b.y, "z": b.z,
                "guild": b.guild_id, "rev": rev,
            }
            for b in snapshot.bases
        ],
    )


def _write_chests(conn: sqlite3.Connection, snapshot: WorldSnapshot, rev: int) -> None:
    conn.executemany(
        """
        INSERT INTO chests(container_guid, base_guid, object_type, x, y, z,
                           lock_code, slot_count, guild_id, kind, lockable,
                           has_container, last_seen_rev)
        VALUES (:guid, :base, :type, :x, :y, :z, :lock, :slots, :guild, :kind,
                :lockable, :has_container, :rev)
        ON CONFLICT(container_guid) DO UPDATE SET
            base_guid     = excluded.base_guid,
            object_type   = excluded.object_type,
            x = excluded.x, y = excluded.y, z = excluded.z,
            lock_code     = excluded.lock_code,
            slot_count    = excluded.slot_count,
            guild_id      = excluded.guild_id,
            kind          = excluded.kind,
            lockable      = excluded.lockable,
            has_container = excluded.has_container,
            last_seen_rev = excluded.last_seen_rev
        """,
        [
            {
                "guid": c.container_guid, "base": c.base_guid,
                "type": c.object_type, "x": c.x, "y": c.y, "z": c.z,
                "lock": c.lock_code, "slots": c.slot_count,
                "guild": c.guild_id, "kind": c.kind,
                "lockable": int(c.lockable), "has_container": int(c.has_container),
                "rev": rev,
            }
            for c in snapshot.chests
        ],
    )

    # Slots are replaced wholesale per chest rather than diffed: the slot array
    # is small and a diff would have to reason about moves it cannot see.
    guids = [c.container_guid for c in snapshot.chests]
    conn.executemany(
        "DELETE FROM slots WHERE container_guid = ?", [(g,) for g in guids]
    )
    conn.executemany(
        "INSERT INTO slots(container_guid, slot_index, item_id, stack_count) "
        "VALUES (?, ?, ?, ?)",
        [
            (c.container_guid, s.slot_index, s.item_id, s.stack_count)
            for c in snapshot.chests
            for s in c.slots
        ],
    )
