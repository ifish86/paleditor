"""Ingest: revs, preservation of app-owned rows, and format refusals."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paleditor import db, ingest
from paleditor.errors import SaveFormatError


def test_first_ingest_writes_rows_and_records_the_rev(config, conn):
    result = ingest.run(config, conn=conn)
    assert result.rev == 1
    assert result.chest_count == 8
    assert result.base_count == 3

    row = conn.execute("SELECT * FROM ingests WHERE rev = 1").fetchone()
    assert row["status"] == "ok"
    assert row["chest_count"] == 8
    assert row["save_sha256"]
    assert row["finished_at"]
    assert db.current_rev(conn) == 1


def test_slot_rows_match_the_save(config, conn):
    ingest.run(config, conn=conn)
    rows = conn.execute(
        "SELECT slot_index, item_id, stack_count FROM slots "
        "WHERE container_guid = ? AND item_id IS NOT NULL ORDER BY slot_index",
        ("6d9eb0f3-3c70-42e6-b8e2-4e5274d02609",),
    ).fetchall()
    # Only occupied slots are stored, so slot 2 is simply absent.
    assert [(r["slot_index"], r["item_id"], r["stack_count"]) for r in rows] == [
        (0, "Wood_Fine", 6432),
        (1, "Fiber", 3464),
        (3, "Coal", 8018),
    ]
    capacity = conn.execute(
        "SELECT slot_count FROM chests WHERE container_guid = ?", ("6d9eb0f3-3c70-42e6-b8e2-4e5274d02609",)
    ).fetchone()["slot_count"]
    assert capacity == 40, "slot_count is the container's capacity, not its contents"


def test_a_second_ingest_opens_a_new_rev(config, conn):
    ingest.run(config, conn=conn)
    second = ingest.run(config, conn=conn)
    assert second.rev == 2
    revs = [r["last_seen_rev"] for r in conn.execute("SELECT last_seen_rev FROM chests")]
    assert set(revs) == {2}


def test_a_dismantled_chest_keeps_its_old_rev_instead_of_vanishing(
    config, conn, save_dir: Path
):
    """The orphan case from the proposal.

    A chest that stops appearing must stay visible with its last_seen_rev, so
    the UI can mark it orphaned rather than have it silently disappear.
    """
    ingest.run(config, conn=conn)

    world = json.loads((save_dir / "Level.sav").read_text())
    world["objects"] = [
        o for o in world["objects"] if o.get("container") != "0bd0dc7c-ed4e-29d3-9daf-74b4b749f06d"
    ]
    (save_dir / "Level.sav").write_text(json.dumps(world))

    second = ingest.run(config, conn=conn)
    assert second.chest_count == 7
    assert second.orphaned == 1

    orphan = conn.execute(
        "SELECT last_seen_rev FROM chests WHERE container_guid = ?",
        ("0bd0dc7c-ed4e-29d3-9daf-74b4b749f06d",),
    ).fetchone()
    assert orphan is not None, "the dismantled chest must still be in the table"
    assert orphan["last_seen_rev"] == 1 < second.rev


def test_ingest_never_touches_nicknames_or_queued_edits(config, conn):
    """What makes nicknames stable across a reparse."""
    ingest.run(config, conn=conn)
    guid = conn.execute("SELECT container_guid FROM chests LIMIT 1").fetchone()[0]
    conn.execute(
        "INSERT INTO chest_meta(container_guid, nickname, notes, updated_at) "
        "VALUES (?, 'Ammo dump', 'top shelf', ?)",
        (guid, db.utcnow()),
    )
    conn.execute(
        "INSERT INTO pending_edits(container_guid, slot_index, item_id, "
        "stack_count, requested_by, status, requested_at) "
        "VALUES (?, 2, 'Wood', 10, 'friend', 'queued', ?)",
        (guid, db.utcnow()),
    )
    conn.commit()

    ingest.run(config, conn=conn)

    meta = conn.execute(
        "SELECT nickname, notes FROM chest_meta WHERE container_guid = ?", (guid,)
    ).fetchone()
    assert meta["nickname"] == "Ammo dump"
    assert meta["notes"] == "top shelf"
    edit = conn.execute("SELECT status FROM pending_edits").fetchone()
    assert edit["status"] == "queued"


def test_refuses_a_save_with_no_chests_and_records_the_failure(
    config, conn, save_dir: Path
):
    """Ingest validating its own expectations.

    A parse that yields nothing must not replace the last good rev.
    """
    ingest.run(config, conn=conn)
    world = json.loads((save_dir / "Level.sav").read_text())
    world["objects"] = []
    (save_dir / "Level.sav").write_text(json.dumps(world))

    with pytest.raises(SaveFormatError, match="no chests"):
        ingest.run(config, conn=conn)

    failed = conn.execute(
        "SELECT status, error FROM ingests ORDER BY rev DESC LIMIT 1"
    ).fetchone()
    assert failed["status"] == "failed"
    assert "no chests" in failed["error"]
    # The last good rev is still the one the UI reads from.
    assert db.current_rev(conn) == 1
    assert conn.execute("SELECT COUNT(*) FROM chests").fetchone()[0] == 8


def test_a_failed_ingest_leaves_the_previous_data_intact(config, conn, save_dir: Path):
    ingest.run(config, conn=conn)
    (save_dir / "Level.sav").write_text("this is not json")
    with pytest.raises(SaveFormatError):
        ingest.run(config, conn=conn)
    assert conn.execute("SELECT COUNT(*) FROM chests").fetchone()[0] == 8
    assert conn.execute("SELECT COUNT(*) FROM slots").fetchone()[0] == 9


def test_unknown_item_ids_are_recorded_as_observed(config, conn):
    ingest.run(config, conn=conn)
    row = conn.execute(
        "SELECT display_name, provenance FROM items WHERE item_id = 'Paldium'"
    ).fetchone()
    assert row["provenance"] == "observed"
    # Rendered as the raw string rather than hidden.
    assert row["display_name"] == "Paldium"


def test_seeded_items_keep_their_curated_names(config, conn):
    ingest.run(config, conn=conn)
    row = conn.execute(
        "SELECT display_name, provenance, max_stack FROM items WHERE item_id = 'PalSphere'"
    ).fetchone()
    assert row["provenance"] == "seed"
    assert row["display_name"] == "Pal Sphere"
    # No stack cap is guessed: the API rejects edits above a cap, so an invented
    # one would block a legitimate edit.
    assert row["max_stack"] is None
