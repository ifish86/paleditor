"""Save extraction: the field paths, chest detection and base grouping."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paleditor.errors import SaveFormatError
from paleditor.saves import get_backend
from paleditor.saves.extract import snapshot_from_parsed
from paleditor.saves.fieldpaths import BASE_ASSIGNMENT_RADIUS


@pytest.fixture
def snapshot(save_dir: Path):
    return get_backend("fixture").read_snapshot(save_dir / "Level.sav")


def test_reads_every_chest_and_base(snapshot):
    assert len(snapshot.bases) == 3
    assert len(snapshot.chests) == 5


def test_ignores_containers_that_are_not_browsable_chests(snapshot):
    # The fixture contains a PalBox. "box" is a chest name hint, so this is the
    # case the explicit exclusion list exists for.
    assert all("palbox" not in (c.object_type or "").lower() for c in snapshot.chests)


def test_reads_slot_contents_with_stack_counts(snapshot):
    chest = next(c for c in snapshot.chests if c.container_guid.startswith("aaaa0001"))
    filled = {s.item_id: s.stack_count for s in chest.slots if s.item_id}
    assert filled == {"PalSphere": 20, "Wood": 99, "Stone": 50}


def test_records_empty_slots_so_an_edit_can_target_them(snapshot):
    chest = next(c for c in snapshot.chests if c.container_guid.startswith("aaaa0004"))
    assert chest.slot_count == 8
    assert all(s.item_id is None and s.stack_count == 0 for s in chest.slots)
    assert [s.slot_index for s in chest.slots] == list(range(8))


def test_finds_lock_codes_and_reports_them_available(snapshot):
    assert snapshot.lock_codes_available is True
    codes = {c.container_guid[:8]: c.lock_code for c in snapshot.chests}
    assert codes["aaaa0001"] == "1234"
    assert codes["aaaa0003"] == "9876"
    # A chest with no PasswordLock module reads as None, not as "unlocked".
    assert codes["aaaa0002"] is None


def test_groups_chests_by_nearest_base(snapshot):
    by_guid = {c.container_guid[:8]: c.base_guid for c in snapshot.chests}
    assert by_guid["aaaa0001"].startswith("11111111")
    assert by_guid["aaaa0003"].startswith("22222222")
    assert by_guid["aaaa0004"].startswith("33333333")


def test_leaves_a_distant_chest_unassigned_rather_than_misattributing_it(snapshot):
    far = next(c for c in snapshot.chests if c.container_guid.startswith("aaaa0005"))
    assert far.base_guid is None
    assert any("not within range" in w for w in snapshot.warnings)


def test_lock_codes_unavailable_when_the_field_has_moved(save_dir: Path):
    """The degradation path: a game update moves the lock field.

    The app must report contents-only rather than showing every chest as
    unlocked.
    """
    world = json.loads((save_dir / "Level.sav").read_text())
    objects = world["properties"]["worldSaveData"]["value"]["MapObjectSaveData"]["value"]["values"]
    for entry in objects:
        module_map = entry["Model"]["value"].get("ModuleMap", {}).get("value", {})
        module_map.pop("PasswordLock", None)
    snapshot = snapshot_from_parsed(world, backend="test")
    assert snapshot.lock_codes_available is False
    assert all(c.lock_code is None for c in snapshot.chests)
    assert any("contents-only" in w for w in snapshot.warnings)


def test_refuses_a_save_without_world_save_data():
    with pytest.raises(SaveFormatError, match="worldSaveData"):
        snapshot_from_parsed({"properties": {}}, backend="test")


def test_refuses_a_save_whose_object_table_is_unreadable():
    broken = {
        "properties": {
            "worldSaveData": {
                "value": {"MapObjectSaveData": {"value": {"values": "not a list"}}}
            }
        }
    }
    with pytest.raises(SaveFormatError, match="MapObjectSaveData"):
        snapshot_from_parsed(broken, backend="test")


def test_a_chest_just_inside_the_radius_is_assigned():
    world = _one_chest_at(BASE_ASSIGNMENT_RADIUS - 1)
    snapshot = snapshot_from_parsed(world, backend="test")
    assert snapshot.chests[0].base_guid is not None


def test_a_chest_just_outside_the_radius_is_not():
    world = _one_chest_at(BASE_ASSIGNMENT_RADIUS + 1)
    snapshot = snapshot_from_parsed(world, backend="test")
    assert snapshot.chests[0].base_guid is None


def _one_chest_at(distance: float) -> dict:
    guid = "cccc0001-0000-0000-0000-000000000001"
    return {
        "properties": {
            "worldSaveData": {
                "value": {
                    "MapObjectSaveData": {
                        "value": {
                            "values": [
                                {
                                    "MapObjectId": {"value": "ItemChest"},
                                    "WorldLocation": {"value": {"x": distance, "y": 0.0, "z": 0.0}},
                                    "GroupIdBelongTo": {"value": "g"},
                                    "Model": {
                                        "value": {
                                            "ModuleMap": {
                                                "value": {
                                                    "ItemContainer": {
                                                        "value": {
                                                            "ContainerId": {
                                                                "value": {"ID": {"value": guid}}
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    },
                                }
                            ]
                        }
                    },
                    "ItemContainerSaveData": {
                        "value": {guid: {"value": {"Slots": {"value": {"values": []}}}}}
                    },
                    "BaseCampSaveData": {
                        "value": {
                            "dddd0001-0000-0000-0000-000000000001": {
                                "value": {
                                    "Id": {"value": "dddd0001-0000-0000-0000-000000000001"},
                                    "GroupIdBelongTo": {"value": "g"},
                                    "Transform": {
                                        "value": {"translation": {"value": {"x": 0.0, "y": 0.0, "z": 0.0}}}
                                    },
                                }
                            }
                        }
                    },
                }
            }
        }
    }
