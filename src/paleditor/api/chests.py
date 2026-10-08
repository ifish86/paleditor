"""Bases, chests, slots, nicknames and the edit queue."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import auth, db, queries
from ..config import Config
from ..models import EditRequest, EditResponse, MetaUpdate
from .deps import get_config, get_conn, get_session

router = APIRouter(tags=["chests"])


@router.get("/bases")
def list_bases(conn: sqlite3.Connection = Depends(get_conn), _=Depends(get_session)):
    payload = {
        "bases": queries.bases(conn),
        "last_ingest": queries.last_good_ingest(conn),
        "lock_codes_available": queries.lock_codes_available(conn),
    }
    # Counted, not listed. A world holds thousands of containers away from any
    # base, so this is a count plus a link rather than a payload.
    unassigned = queries.count_chests_at_base(conn, None)
    if unassigned:
        payload["unassigned"] = {
            "base_guid": None,
            "name": "Not near a base",
            "chest_count": unassigned,
        }
    return payload


@router.get("/bases/{base_guid}/chests")
def list_chests(
    base_guid: str,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    kinds: str | None = Query(
        default=None,
        description=(
            "Comma-separated: storage, loot, station, lock-only, or 'all'. "
            "Defaults to storage and lock-only, because world loot outnumbers "
            "player chests many times over."
        ),
    ),
    conn: sqlite3.Connection = Depends(get_conn),
    _=Depends(get_session),
):
    if base_guid != "unassigned" and queries.base(conn, base_guid) is None:
        raise HTTPException(status_code=404, detail="no such base")
    selected = _parse_kinds(kinds)
    target = None if base_guid == "unassigned" else base_guid
    total = queries.count_chests_at_base(conn, target, kinds=selected)
    chests = queries.chests_at_base(
        conn, target, limit=limit, offset=offset, kinds=selected
    )
    return {
        "base": queries.base(conn, base_guid) if target else {"base_guid": None, "name": "Not near a base"},
        "chests": chests,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(chests) < total,
    }


@router.get("/chests/search")
def search(
    q: str = Query(min_length=1, max_length=120),
    conn: sqlite3.Connection = Depends(get_conn),
    _=Depends(get_session),
):
    return {"query": q, "chests": queries.search_chests(conn, q)}


@router.get("/chests/{container_guid}")
def get_chest(
    container_guid: str,
    conn: sqlite3.Connection = Depends(get_conn),
    _=Depends(get_session),
):
    chest = queries.chest(conn, container_guid)
    if chest is None:
        raise HTTPException(status_code=404, detail="no such chest")
    return chest


@router.patch("/chests/{container_guid}/meta")
def set_meta(
    container_guid: str,
    body: MetaUpdate,
    conn: sqlite3.Connection = Depends(get_conn),
    _=Depends(get_session),
):
    """Nicknames and notes. App-owned, so ingest never overwrites them."""
    if queries.chest(conn, container_guid) is None:
        raise HTTPException(status_code=404, detail="no such chest")
    with db.transaction(conn):
        conn.execute(
            """
            INSERT INTO chest_meta(container_guid, nickname, notes, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(container_guid) DO UPDATE SET
                nickname = excluded.nickname,
                notes    = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (container_guid, body.nickname, body.notes, db.utcnow()),
        )
    return queries.chest(conn, container_guid)


@router.post(
    "/chests/{container_guid}/edits",
    response_model=EditResponse,
    status_code=status.HTTP_201_CREATED,
)
def queue_edit(
    container_guid: str,
    body: EditRequest,
    config: Config = Depends(get_config),
    conn: sqlite3.Connection = Depends(get_conn),
    session: auth.Session = Depends(get_session),
) -> EditResponse:
    """Queue one slot edit. It does not take effect until the next window."""
    chest = queries.chest(conn, container_guid)
    if chest is None:
        raise HTTPException(status_code=404, detail="no such chest")
    if body.slot_index >= chest["slot_count"]:
        raise HTTPException(
            status_code=422,
            detail=(
                f"slot {body.slot_index} is out of range; this chest has "
                f"{chest['slot_count']} slots"
            ),
        )
    if body.item_id is not None and body.stack_count <= 0:
        raise HTTPException(
            status_code=422,
            detail=(
                "stack_count must be at least 1 when an item is set. To empty "
                "the slot, send item_id = null."
            ),
        )
    if body.item_id is not None:
        cap = conn.execute(
            "SELECT max_stack FROM items WHERE item_id = ?", (body.item_id,)
        ).fetchone()
        if cap and cap["max_stack"] and body.stack_count > cap["max_stack"]:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{body.item_id} stacks to {cap['max_stack']}; "
                    f"{body.stack_count} would be rejected by the game"
                ),
            )

    existing = conn.execute(
        "SELECT id FROM pending_edits WHERE container_guid=? AND slot_index=? "
        "AND status IN ('queued','applying')",
        (container_guid, body.slot_index),
    ).fetchone()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=(
                f"slot {body.slot_index} already has edit {existing['id']} "
                "waiting. Cancel it first."
            ),
        )

    with db.transaction(conn):
        cursor = conn.execute(
            """
            INSERT INTO pending_edits(container_guid, slot_index, item_id,
                                      stack_count, requested_by, status, requested_at)
            VALUES (?, ?, ?, ?, ?, 'queued', ?)
            """,
            (
                container_guid,
                body.slot_index,
                body.item_id,
                body.stack_count,
                session.role,
                db.utcnow(),
            ),
        )
        edit_id = int(cursor.lastrowid)

    row = queries.edit(conn, edit_id)
    assert row is not None
    return EditResponse(**row, expected_window=_next_window(config))


@router.get("/edits")
def list_edits(
    status_filter: str | None = Query(default=None, alias="status"),
    conn: sqlite3.Connection = Depends(get_conn),
    config: Config = Depends(get_config),
    _=Depends(get_session),
):
    allowed = {"queued", "applying", "applied", "failed", "cancelled"}
    if status_filter and status_filter not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {', '.join(sorted(allowed))}",
        )
    return {
        "edits": queries.edits(conn, status=status_filter),
        "queue": queries.queue_depth(conn),
        "next_window": _next_window(config),
    }


@router.delete("/edits/{edit_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_edit(
    edit_id: int,
    conn: sqlite3.Connection = Depends(get_conn),
    session: auth.Session = Depends(get_session),
) -> None:
    """Cancel a queued edit.

    Anyone can cancel their own. Cancelling someone else's needs the owner
    password, which is the second of the two destructive gates.
    """
    row = queries.edit(conn, edit_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no such edit")
    if row["status"] == "applying":
        raise HTTPException(
            status_code=409,
            detail="that edit is being applied right now and cannot be cancelled",
        )
    if row["status"] != "queued":
        raise HTTPException(
            status_code=409,
            detail=f"that edit is already {row['status']}",
        )
    if row["requested_by"] != session.role and not session.is_owner:
        raise HTTPException(
            status_code=403,
            detail="cancelling someone else's edit needs the owner password",
        )
    with db.transaction(conn):
        conn.execute(
            "UPDATE pending_edits SET status='cancelled', applied_at=? WHERE id=?",
            (db.utcnow(), edit_id),
        )


@router.get("/items/{item_id}/icon")
def item_icon(
    item_id: str,
    config: Config = Depends(get_config),
    conn: sqlite3.Connection = Depends(get_conn),
    _=Depends(get_session),
):
    """Serve a downloaded icon, or 404 so the UI falls back to its category."""
    from fastapi.responses import FileResponse

    from .. import db as _db

    row = conn.execute(
        "SELECT icon FROM items WHERE item_id = ?", (item_id,)
    ).fetchone()
    if row is None or not row["icon"]:
        raise HTTPException(status_code=404, detail="no icon for that item")

    directory = _db.icon_dir(config.database.path).resolve()
    path = (directory / row["icon"]).resolve()
    # The stored name is sanitised on the way in; checked again on the way out
    # because this one serves files off disk.
    if directory not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="icon file is missing")
    from ..icons import MEDIA_TYPES

    return FileResponse(
        path,
        media_type=MEDIA_TYPES.get(path.suffix.lstrip("."), "application/octet-stream"),
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/items")
def list_items(
    q: str | None = Query(default=None, max_length=120),
    conn: sqlite3.Connection = Depends(get_conn),
    _=Depends(get_session),
):
    return {"items": queries.items(conn, term=q)}


KNOWN_KINDS = {"storage", "loot", "station", "lock-only", "all"}


def _parse_kinds(raw: str | None) -> tuple[str, ...] | None:
    if not raw:
        return None
    chosen = tuple(part.strip() for part in raw.split(",") if part.strip())
    unknown = set(chosen) - KNOWN_KINDS
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"unknown kind(s): {', '.join(sorted(unknown))}; "
                   f"expected {', '.join(sorted(KNOWN_KINDS))}",
        )
    return chosen


def _next_window(config: Config) -> str | None:
    """When a queued edit is expected to land.

    Every queued edit carries this, because a queued edit not taking effect
    immediately is the one thing about this app that surprises people.
    """
    from datetime import datetime

    from ..scheduler import CronSchedule

    if not config.maintenance.enabled:
        return None
    try:
        return CronSchedule(config.maintenance.schedule).next_after(
            datetime.now()
        ).isoformat(timespec="minutes")
    except Exception:
        return None
