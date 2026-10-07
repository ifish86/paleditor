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
    assert result.chest_count == 5
    assert result.base_count == 3

    row = conn.execute("SELECT * FROM ingests WHERE rev = 1").fetchone()
    assert row["status"] == "ok"
    assert row["chest_count"] == 5
    assert row["save_sha256"]
    assert row["finished_at"]
    assert db.current_rev(conn) == 1


def test_slot_rows_match_the_save(config, conn):
    ingest.run(config, conn=conn)
    rows = conn.execute(
        "SELECT slot_index, item_id, stack_count FROM slots "
        "WHERE container_guid LIKE 'aaaa0001%' AND item_id IS NOT NULL "
        "ORDER BY slot_index"
    ).fetchall()
    assert [(r["slot_index"], r["item_id"], r["stack_count"]) for r in rows] == [
        (0, "PalSphere", 20),
        (1, "Wood", 99),
        (3, "Stone", 50),
    ]


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
    data = world["properties"]["worldSaveData"]["value"]
    data["MapObjectSaveData"]["value"]["values"] = [
        entry
        for entry in data["MapObjectSaveData"]["value"]["values"]
        if "aaaa0002" not in json.dumps(entry)
    ]
    (save_dir / "Level.sav").write_text(json.dumps(world))

    second = ingest.run(config, conn=conn)
    assert second.chest_count == 4
    assert second.orphaned == 1

    orphan = conn.execute(
        "SELECT last_seen_rev FROM chests WHERE container_guid LIKE 'aaaa0002%'"
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
    world["properties"]["worldSaveData"]["value"]["MapObjectSaveData"]["value"]["values"] = []
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
    assert conn.execute("SELECT COUNT(*) FROM chests").fetchone()[0] == 5


def test_a_failed_ingest_leaves_the_previous_data_intact(config, conn, save_dir: Path):
    ingest.run(config, conn=conn)
    (save_dir / "Level.sav").write_text("this is not json")
    with pytest.raises(SaveFormatError):
        ingest.run(config, conn=conn)
    assert conn.execute("SELECT COUNT(*) FROM chests").fetchone()[0] == 5
    assert conn.execute("SELECT COUNT(*) FROM slots").fetchone()[0] == 40


def test_unknown_item_ids_are_recorded_as_observed(config, conn):
    ingest.run(config, conn=conn)
    row = conn.execute(
        "SELECT display_name, provenance FROM items WHERE item_id = 'PalSphere_Mega'"
    ).fetchone()
    assert row["provenance"] == "observed"
    # Rendered as the raw string rather than hidden.
    assert row["display_name"] == "PalSphere_Mega"


def test_seeded_items_keep_their_curated_names(config, conn):
    ingest.run(config, conn=conn)
    row = conn.execute(
        "SELECT display_name, provenance, max_stack FROM items WHERE item_id = 'PalSphere'"
    ).fetchone()
    assert row["provenance"] == "seed"
    assert row["display_name"] == "Pal Sphere"
    assert row["max_stack"] == 50
