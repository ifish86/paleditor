"""Reading item ids and names out of the game's own data.

The picker used to offer only the few hundred ids that already existed
somewhere in the world, so a chest could not be given anything new. And the
names were guesses: Wood_Fine is Hardwood, not "Quality Wood"; CopperOre is
just Ore. Both come from the game's localisation table now.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from paleditor import gamedata
from paleditor.errors import PaleditorError


def counted(text: str) -> bytes:
    raw = text.encode("ascii") + b"\x00"
    return struct.pack("<i", len(raw)) + raw


def name_table(rows: dict[str, str]) -> bytes:
    """The shape the real table has: row key, then the display string."""
    out = b"\x00" * 16
    for item_id, display in rows.items():
        out += counted(f"ITEM_NAME_{item_id}_TextData") + counted(display)
    return out


# -- parsing ---------------------------------------------------------------


def test_rows_are_read_as_id_then_name(monkeypatch, tmp_path):
    table = name_table({"Wood_Fine": "Hardwood", "CopperOre": "Ore"})
    monkeypatch.setattr(gamedata.PakFile, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(gamedata.PakFile, "read_file", lambda self, path: table)

    names = gamedata.item_names(tmp_path / "fake.pak")
    assert names == {"Wood_Fine": "Hardwood", "CopperOre": "Ore"}


def test_a_table_with_no_rows_is_an_error(monkeypatch, tmp_path):
    """Better than silently importing nothing: it means the layout moved."""
    monkeypatch.setattr(gamedata.PakFile, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(gamedata.PakFile, "read_file", lambda self, path: b"\x00" * 64)
    with pytest.raises(PaleditorError, match="no ITEM_NAME rows"):
        gamedata.item_names(tmp_path / "fake.pak")


def test_utf16_display_names_survive(monkeypatch, tmp_path):
    key = counted("ITEM_NAME_Fancy_TextData")
    value = "Pal Sphere™".encode("utf-16-le") + b"\x00\x00"
    table = b"\x00" * 8 + key + struct.pack("<i", -(len(value) // 2)) + value
    monkeypatch.setattr(gamedata.PakFile, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(gamedata.PakFile, "read_file", lambda self, path: table)
    assert gamedata.item_names(tmp_path / "f.pak")["Fancy"] == "Pal Sphere™"


# -- finding the pak -------------------------------------------------------


def test_the_pak_is_found_from_the_save_directory(tmp_path):
    """<root>/Pal/Saved/SaveGames/0/<world> -> <root>/Pal/Content/Paks"""
    pal = tmp_path / "server" / "Pal"
    save = pal / "Saved" / "SaveGames" / "0" / "WORLDID"
    save.mkdir(parents=True)
    paks = pal / "Content" / "Paks"
    paks.mkdir(parents=True)
    (paks / "Pal-LinuxServer.pak").write_bytes(b"x")
    assert gamedata.find_pak(save) == paks / "Pal-LinuxServer.pak"


def test_no_pak_is_not_an_error(tmp_path):
    save = tmp_path / "a" / "b" / "c" / "d"
    save.mkdir(parents=True)
    assert gamedata.find_pak(save) is None


# -- the catalogue ---------------------------------------------------------


def test_game_names_outrank_hand_written_ones(conn):
    """items.json said "Quality Wood". The game says Hardwood."""
    from paleditor import catalog

    catalog.seed(conn)
    before = conn.execute(
        "SELECT display_name, provenance FROM items WHERE item_id = 'Wood_Fine'"
    ).fetchone()
    assert before["provenance"] == "seed"

    catalog.import_game_names(conn, {"Wood_Fine": "Hardwood"})
    after = conn.execute(
        "SELECT display_name, provenance FROM items WHERE item_id = 'Wood_Fine'"
    ).fetchone()
    assert after["display_name"] == "Hardwood"
    assert after["provenance"] == "gamefiles"


def test_a_confirmed_name_still_wins(conn):
    """Something read back from a live dump by hand outranks even the table."""
    from paleditor import catalog

    conn.execute(
        "INSERT INTO items(item_id, display_name, category, provenance) "
        "VALUES ('Thing', 'Checked By Hand', 'material', 'confirmed')"
    )
    conn.commit()
    catalog.import_game_names(conn, {"Thing": "From The Table"})
    row = conn.execute(
        "SELECT display_name FROM items WHERE item_id = 'Thing'"
    ).fetchone()
    assert row["display_name"] == "Checked By Hand"


def test_items_the_world_has_never_held_are_still_offered(conn):
    """The whole point: a chest can be given something new."""
    from paleditor import catalog

    catalog.import_game_names(conn, {"NeverSeen": "Never Seen", "Wood": "Wood"})
    rows = {
        r["item_id"]: r["in_world"]
        for r in conn.execute("SELECT item_id, in_world FROM items")
    }
    assert "NeverSeen" in rows
    assert rows["NeverSeen"] == 0


def test_items_present_in_the_world_are_flagged(config, conn):
    from paleditor import catalog, ingest

    ingest.run(config, conn=conn)
    catalog.import_game_names(conn, {"Wood_Fine": "Hardwood", "NeverSeen": "Never"})
    rows = {
        r["item_id"]: r["in_world"]
        for r in conn.execute("SELECT item_id, in_world FROM items")
    }
    assert rows["Wood_Fine"] == 1
    assert rows["NeverSeen"] == 0


def test_the_picker_lists_items_in_the_world_first(config, conn, friend_client):
    from paleditor import catalog

    catalog.import_game_names(conn, {"ZzzNeverSeen": "Aaa Never Seen"})
    items = friend_client.get("/api/items").json()["items"]
    seen_absent = next(i for i, it in enumerate(items) if it["item_id"] == "ZzzNeverSeen")
    first_present = next(i for i, it in enumerate(items) if it["in_world"])
    assert first_present < seen_absent, "items in the world should sort first"
