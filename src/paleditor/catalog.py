"""The item catalogue: seeding the curated rows, naming the observed ones.

Separate from both the app and ingest because both need it, and the order
matters: seeding must happen before ingest records unknown ids, or a seeded
item gets filed as 'observed' with its raw id for a display name.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from importlib import resources

log = logging.getLogger(__name__)


def seed_rows() -> list[dict]:
    try:
        raw = resources.files("paleditor").joinpath("data/items.json").read_text("utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        log.warning("no item seed file found; the catalogue starts empty")
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.error("item seed file is not valid JSON: %s", exc)
        return []
    rows = payload.get("items", []) if isinstance(payload, dict) else payload
    return [row for row in rows if isinstance(row, dict) and row.get("item_id")]


def seed(conn: sqlite3.Connection) -> int:
    """Upsert the shipped catalogue.

    Rows an operator marked 'confirmed' are left alone: those were read back
    from a live dump and outrank anything shipped in the repo.
    """
    rows = seed_rows()
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO items(item_id, display_name, category, max_stack, provenance)
        VALUES (:item_id, :display_name, :category, :max_stack, 'seed')
        ON CONFLICT(item_id) DO UPDATE SET
            display_name = excluded.display_name,
            category     = excluded.category,
            max_stack    = excluded.max_stack,
            provenance   = 'seed'
        WHERE items.provenance != 'confirmed'
        """,
        [
            {
                "item_id": row["item_id"],
                "display_name": row.get("display_name") or row["item_id"],
                "category": row.get("category"),
                "max_stack": row.get("max_stack"),
            }
            for row in rows
        ],
    )
    conn.commit()
    return len(rows)


def record_observed(conn: sqlite3.Connection) -> int:
    """File every unknown id the world holds as 'observed'.

    That gives the curation job a worklist. An unnamed item still renders as
    its raw id in the UI rather than being hidden.
    """
    cursor = conn.execute(
        """
        INSERT INTO items(item_id, display_name, category, max_stack, provenance)
        SELECT DISTINCT s.item_id, s.item_id, NULL, NULL, 'observed'
        FROM slots s
        WHERE s.item_id IS NOT NULL
          AND s.item_id NOT IN (SELECT item_id FROM items)
        """
    )
    conn.commit()
    return cursor.rowcount or 0
