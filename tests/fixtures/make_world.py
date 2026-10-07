"""Generate a small GVAS-shaped world.json for tests and local development.

Committed alongside its output so the fixture is reproducible. Run with:
    python tests/fixtures/make_world.py
"""

from __future__ import annotations

import json
from pathlib import Path

BASES = [
    ("11111111-1111-1111-1111-111111111111", "guild-a", 0.0, 0.0, 100.0),
    ("22222222-2222-2222-2222-222222222222", "guild-a", 50000.0, 20000.0, 150.0),
    ("33333333-3333-3333-3333-333333333333", "guild-b", -80000.0, 60000.0, 90.0),
]

# (container guid, object type, guild, x, y, z, lock code, slot contents)
CHESTS = [
    ("aaaa0001-0000-0000-0000-000000000001", "ItemChest", "guild-a", 300.0, 400.0, 100.0, "1234",
     [(0, "PalSphere", 20), (1, "Wood", 99), (3, "Stone", 50)]),
    ("aaaa0002-0000-0000-0000-000000000002", "ItemChest", "guild-a", -250.0, 600.0, 100.0, None,
     [(0, "Bullet_Normal", 500)]),
    ("aaaa0003-0000-0000-0000-000000000003", "DeathPenaltyChest", "guild-a", 50200.0, 20100.0, 150.0, "9876",
     [(0, "PalSphere_Mega", 10), (1, "Ingot", 120), (2, "Cloth", 30), (5, "Coal", 77)]),
    ("aaaa0004-0000-0000-0000-000000000004", "ItemChest", "guild-b", -80100.0, 60050.0, 90.0, "0000",
     []),
    # Far from every base camp: must land unassigned rather than attached to
    # the nearest base hundreds of thousands of units away.
    ("aaaa0005-0000-0000-0000-000000000005", "ItemChest", "guild-b", 400000.0, 400000.0, 50.0, None,
     [(0, "Paldium", 300)]),
]

SLOTS_PER_CHEST = 8


def slot(index: int, item_id: str | None, count: int) -> dict:
    return {
        "SlotIndex": {"value": index},
        "ItemId": {"value": {"StaticId": {"value": item_id or "None"}}},
        "StackCount": {"value": count},
    }


def build() -> dict:
    map_objects = []
    containers: dict[str, dict] = {}

    for guid, obj_type, guild, x, y, z, lock, contents in CHESTS:
        filled = {i: (item, n) for i, item, n in contents}
        slots = [
            slot(i, *filled.get(i, (None, 0)))
            for i in range(SLOTS_PER_CHEST)
        ]
        containers[guid] = {"value": {"Slots": {"value": {"values": slots}}}}

        module_map = {
            "ItemContainer": {
                "value": {"ContainerId": {"value": {"ID": {"value": guid}}}}
            }
        }
        if lock is not None:
            module_map["PasswordLock"] = {"value": {"Password": {"value": lock}}}

        map_objects.append(
            {
                "MapObjectId": {"value": obj_type},
                "WorldLocation": {"value": {"x": x, "y": y, "z": z}},
                "GroupIdBelongTo": {"value": guild},
                "Model": {"value": {"ModuleMap": {"value": module_map}}},
            }
        )

    # A non-chest object that owns a container: must be ignored by v1.
    map_objects.append(
        {
            "MapObjectId": {"value": "PalBox"},
            "WorldLocation": {"value": {"x": 10.0, "y": 10.0, "z": 100.0}},
            "GroupIdBelongTo": {"value": "guild-a"},
            "Model": {"value": {"ModuleMap": {"value": {}}}},
        }
    )

    base_camps = {
        guid: {
            "value": {
                "Id": {"value": guid},
                "GroupIdBelongTo": {"value": guild},
                "Transform": {"value": {"translation": {"value": {"x": x, "y": y, "z": z}}}},
            }
        }
        for guid, guild, x, y, z in BASES
    }

    return {
        "header": {"magic": "GVAS", "note": "test fixture, not a real save"},
        "properties": {
            "worldSaveData": {
                "value": {
                    "MapObjectSaveData": {"value": {"values": map_objects}},
                    "ItemContainerSaveData": {"value": containers},
                    "BaseCampSaveData": {"value": base_camps},
                }
            }
        },
    }


if __name__ == "__main__":
    out = Path(__file__).with_name("world.json")
    out.write_text(json.dumps(build(), indent=1), encoding="utf-8")
    print(f"wrote {out}")
