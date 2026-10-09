"""Every read the API performs, in one module.

Each of these is a small indexed query against the ingested rows. The frontend
never sees a save file, a GVAS structure or a parse.
"""

from __future__ import annotations

import sqlite3
from typing import Sequence

from . import db


def bases(conn: sqlite3.Connection) -> list[dict]:
    rev = db.current_rev(conn)
    rows = conn.execute(
        """
        SELECT b.base_guid, b.name, b.x, b.y, b.z, b.guild_id, b.last_seen_rev,
               SUM(CASE WHEN c.kind = 'storage' THEN 1 ELSE 0 END)    AS chest_count,
               SUM(CASE WHEN c.kind = 'loot'    THEN 1 ELSE 0 END)    AS loot_count,
               SUM(CASE WHEN c.lock_code IS NOT NULL THEN 1 ELSE 0 END) AS locked_count,
               (SELECT COUNT(DISTINCT p.container_guid)
                  FROM pending_edits p
                  JOIN chests pc ON pc.container_guid = p.container_guid
                 WHERE pc.base_guid = b.base_guid
                   AND p.status IN ('queued','applying'))             AS pending_chests
          FROM bases b
          LEFT JOIN chests c ON c.base_guid = b.base_guid
         GROUP BY b.base_guid
         ORDER BY b.name IS NULL, b.name, b.base_guid
        """
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["stale"] = rev is not None and row["last_seen_rev"] < rev
        out.append(item)
    return out


def base(conn: sqlite3.Connection, base_guid: str) -> dict | None:
    row = conn.execute("SELECT * FROM bases WHERE base_guid = ?", (base_guid,)).fetchone()
    return dict(row) if row else None


# What the chest screens show by default. World loot respawns constantly and
# outnumbers player storage roughly forty to one, so it is counted but not
# listed unless asked for.
DEFAULT_KINDS = ("storage", "lock-only")


def _kind_clause(kinds: Sequence[str] | None) -> tuple[str, tuple]:
    chosen = tuple(kinds) if kinds else DEFAULT_KINDS
    if "all" in chosen:
        return "", ()
    placeholders = ", ".join("?" for _ in chosen)
    return f" AND c.kind IN ({placeholders})", chosen


def count_chests_at_base(
    conn: sqlite3.Connection,
    base_guid: str | None,
    *,
    kinds: Sequence[str] | None = None,
) -> int:
    where = "base_guid IS NULL" if base_guid is None else "base_guid = ?"
    params: tuple = () if base_guid is None else (base_guid,)
    clause, kind_params = _kind_clause(kinds)
    return conn.execute(
        f"SELECT COUNT(*) FROM chests c WHERE {where.replace('base_guid', 'c.base_guid')}"
        f"{clause}",
        (*params, *kind_params),
    ).fetchone()[0]


def chests_at_base(
    conn: sqlite3.Connection,
    base_guid: str | None,
    *,
    limit: int = 200,
    offset: int = 0,
    kinds: Sequence[str] | None = None,
) -> list[dict]:
    """One page of chests.

    Paged because a world holds thousands of containers. The reference world
    has 2,606 world-loot containers against 72 player chests, and an unbounded
    query would ship all of them in one response.
    """
    rev = db.current_rev(conn)
    where = "c.base_guid IS NULL" if base_guid is None else "c.base_guid = ?"
    params: tuple = () if base_guid is None else (base_guid,)
    clause, kind_params = _kind_clause(kinds)
    params = (*params, *kind_params)
    rows = conn.execute(
        f"""
        SELECT c.container_guid, c.base_guid, c.object_type, c.x, c.y, c.z,
               c.lock_code, c.slot_count, c.last_seen_rev, c.kind,
               c.lockable, c.has_container,
               m.nickname, m.notes,
               (SELECT COUNT(*) FROM slots s
                 WHERE s.container_guid = c.container_guid
                   AND s.item_id IS NOT NULL)                AS filled_slots,
               (SELECT COUNT(*) FROM pending_edits p
                 WHERE p.container_guid = c.container_guid
                   AND p.status IN ('queued','applying'))    AS pending_count
          FROM chests c
          LEFT JOIN chest_meta m ON m.container_guid = c.container_guid
         WHERE {where}{clause}
         ORDER BY m.nickname IS NULL, m.nickname, c.x, c.y
         LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
    ).fetchall()
    return [_decorate_chest(dict(row), rev) for row in rows]


def chest(conn: sqlite3.Connection, container_guid: str) -> dict | None:
    rev = db.current_rev(conn)
    row = conn.execute(
        """
        SELECT c.*, m.nickname, m.notes, m.updated_at AS meta_updated_at,
               b.name AS base_name,
               (SELECT COUNT(*) FROM slots s
                 WHERE s.container_guid = c.container_guid
                   AND s.item_id IS NOT NULL)                AS filled_slots,
               (SELECT COUNT(*) FROM pending_edits p
                 WHERE p.container_guid = c.container_guid
                   AND p.status IN ('queued','applying'))    AS pending_count
          FROM chests c
          LEFT JOIN chest_meta m ON m.container_guid = c.container_guid
          LEFT JOIN bases b      ON b.base_guid      = c.base_guid
         WHERE c.container_guid = ?
        """,
        (container_guid,),
    ).fetchone()
    if row is None:
        return None
    out = _decorate_chest(dict(row), rev)
    out["slots"] = slots(conn, container_guid, capacity=out.get("slot_count") or 0)
    out["pending_edits"] = edits(conn, container_guid=container_guid, open_only=True)
    return out


def slots(
    conn: sqlite3.Connection, container_guid: str, *, capacity: int = 0
) -> list[dict]:
    """The chest's full slot grid.

    The save stores only occupied slots, but the UI draws a grid of the
    container's capacity and a queued edit can target an index that holds
    nothing yet. Unoccupied indices are filled in here so every addressable
    slot has a row, including one carrying a pending edit.
    """
    rows = conn.execute(
        """
        SELECT s.slot_index, s.item_id, s.stack_count,
               i.display_name, i.category, i.max_stack, i.icon,
               p.id          AS pending_edit_id,
               p.item_id     AS pending_item_id,
               p.stack_count AS pending_stack_count,
               p.status      AS pending_status
          FROM slots s
          LEFT JOIN items i ON i.item_id = s.item_id
          LEFT JOIN pending_edits p
                 ON p.container_guid = s.container_guid
                AND p.slot_index     = s.slot_index
                AND p.status IN ('queued','applying')
         WHERE s.container_guid = ?
         ORDER BY s.slot_index
        """,
        (container_guid,),
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        # An unknown item_id renders as its raw string rather than being hidden.
        item["display_name"] = item["display_name"] or item["item_id"]
        item["has_pending"] = item["pending_edit_id"] is not None
        # Every occupied slot gets a category so the grid is readable even
        # where no icon was found, which is most of them.
        item["category"] = item["category"] or _category(item["item_id"])
        out.append(item)

    stored = {item["slot_index"] for item in out}
    pending = conn.execute(
        "SELECT id, slot_index, item_id, stack_count, status FROM pending_edits "
        "WHERE container_guid = ? AND status IN ('queued','applying')",
        (container_guid,),
    ).fetchall()
    pending_by_index = {row["slot_index"]: row for row in pending}

    highest = max(stored | set(pending_by_index) | {-1}) + 1
    for index in range(max(capacity, highest)):
        if index in stored:
            continue
        edit = pending_by_index.get(index)
        out.append(
            {
                "slot_index": index,
                "item_id": None,
                "stack_count": 0,
                "display_name": None,
                "category": None,
                "max_stack": None,
                "icon": None,
                "pending_edit_id": edit["id"] if edit else None,
                "pending_item_id": edit["item_id"] if edit else None,
                "pending_stack_count": edit["stack_count"] if edit else None,
                "pending_status": edit["status"] if edit else None,
                "has_pending": edit is not None,
            }
        )
    out.sort(key=lambda item: item["slot_index"])
    return out


def search_chests(conn: sqlite3.Connection, term: str, *, limit: int = 100) -> list[dict]:
    """Filter across nickname, lock code and contained item id or name."""
    rev = db.current_rev(conn)
    like = f"%{term.strip()}%"
    rows = conn.execute(
        """
        SELECT DISTINCT c.container_guid, c.base_guid, c.object_type, c.x, c.y, c.z,
               c.lock_code, c.slot_count, c.last_seen_rev, c.kind,
               c.lockable, c.has_container,
               m.nickname, m.notes, b.name AS base_name,
               (SELECT COUNT(*) FROM slots s2
                 WHERE s2.container_guid = c.container_guid
                   AND s2.item_id IS NOT NULL)                AS filled_slots,
               (SELECT COUNT(*) FROM pending_edits p
                 WHERE p.container_guid = c.container_guid
                   AND p.status IN ('queued','applying'))     AS pending_count
          FROM chests c
          LEFT JOIN chest_meta m ON m.container_guid = c.container_guid
          LEFT JOIN bases b      ON b.base_guid      = c.base_guid
          LEFT JOIN slots s      ON s.container_guid = c.container_guid
          LEFT JOIN items i      ON i.item_id        = s.item_id
         WHERE (m.nickname    LIKE ?
            OR c.lock_code   LIKE ?
            OR s.item_id     LIKE ?
            OR i.display_name LIKE ?
            OR c.container_guid LIKE ?)
           AND c.kind IN ('storage', 'lock-only')
         ORDER BY m.nickname IS NULL, m.nickname
         LIMIT ?
        """,
        (like, like, like, like, like, limit),
    ).fetchall()
    return [_decorate_chest(dict(row), rev) for row in rows]


def edits(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    container_guid: str | None = None,
    open_only: bool = False,
    limit: int = 500,
) -> list[dict]:
    clauses, params = [], []
    if status:
        clauses.append("p.status = ?")
        params.append(status)
    if open_only:
        clauses.append("p.status IN ('queued','applying')")
    if container_guid:
        clauses.append("p.container_guid = ?")
        params.append(container_guid)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT p.*, m.nickname, i.display_name
          FROM pending_edits p
          LEFT JOIN chest_meta m ON m.container_guid = p.container_guid
          LEFT JOIN items i      ON i.item_id        = p.item_id
          {where}
         ORDER BY CASE p.status
                    WHEN 'applying' THEN 0 WHEN 'queued' THEN 1
                    WHEN 'failed'   THEN 2 ELSE 3 END,
                  p.requested_at DESC
         LIMIT ?
        """,
        params,
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["display_name"] = item["display_name"] or item["item_id"]
        item["is_clear"] = item["item_id"] is None or item["stack_count"] <= 0
        out.append(item)
    return out


def edit(conn: sqlite3.Connection, edit_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM pending_edits WHERE id = ?", (edit_id,)).fetchone()
    return dict(row) if row else None


# Items already in the world come first: those are the ones somebody is most
# likely to be topping up. The rest of the game's catalogue follows, so a chest
# can be given something it has never held.
ITEM_ORDER = "ORDER BY in_world DESC, display_name, item_id"


def items(conn: sqlite3.Connection, *, term: str | None = None) -> list[dict]:
    if term:
        like = f"%{term.strip()}%"
        rows = conn.execute(
            f"SELECT * FROM items WHERE item_id LIKE ? OR display_name LIKE ? "
            f"{ITEM_ORDER} LIMIT 500",
            (like, like),
        ).fetchall()
    else:
        rows = conn.execute(f"SELECT * FROM items {ITEM_ORDER}").fetchall()
    return [dict(row) for row in rows]


def queue_depth(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM pending_edits GROUP BY status"
    ).fetchall()
    counts = {row["status"]: row["n"] for row in rows}
    return {
        status: counts.get(status, 0)
        for status in ("queued", "applying", "applied", "failed", "cancelled")
    }


def last_ingest(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT * FROM ingests ORDER BY rev DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def last_good_ingest(conn: sqlite3.Connection) -> dict | None:
    row = db.latest_ingest(conn)
    return dict(row) if row else None


def lock_codes_available(conn: sqlite3.Connection) -> bool:
    """True when at least one chest in the current rev carries a lock code.

    Drives the contents-only degradation in the UI: with the field missing,
    showing every chest as unlocked would be a lie.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM chests WHERE lock_code IS NOT NULL"
    ).fetchone()
    return bool(row and row["n"])


def _category(item_id: str | None) -> str | None:
    if not item_id:
        return None
    from .catalog import categorise

    return categorise(item_id)


def _decorate_chest(item: dict, rev: int | None) -> dict:
    item["stale"] = rev is not None and item.get("last_seen_rev", rev) < rev
    if item.get("slot_count") and "filled_slots" in item:
        item["fill_ratio"] = round(item["filled_slots"] / item["slot_count"], 3)
    else:
        item["fill_ratio"] = 0.0
    return item
