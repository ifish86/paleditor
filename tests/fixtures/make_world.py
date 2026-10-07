"""Generate the JSON fixture world used by the test suite.

Modelled on the real server world read on 2026-10-07, so the tests exercise the
shapes that actually occur: capacity larger than the number of stored slots,
world loot alongside player storage, a chest that is lockable with no code, and
a door that carries a code but holds nothing.

    python tests/fixtures/make_world.py
"""

from __future__ import annotations

import json
from pathlib import Path

BASES = [
    ("34c1b73f-5074-4bcf-b672-6d26121bfc50", -358127.0, 269555.0, 7873.0),
    ("04e97c45-21db-4b1e-9a3c-1f0d2e8b7a61", -168324.0, 291607.0, -586.0),
    ("36b74830-075e-4a39-a6d6-af8ebd4bd011", -11848.0, 292191.0, 14985.0),
]

# (guid, object_type, base index, dx, dy, capacity, lock_code, lockable, slots)
OBJECTS = [
    ("6d9eb0f3-3c70-42e6-b8e2-4e5274d02609", "Container01_Iron", 2, 300.0, 400.0, 40,
     "7826", True, [(0, "Wood_Fine", 6432), (1, "Fiber", 3464), (3, "Coal", 8018)]),
    ("0bd0dc7c-ed4e-29d3-9daf-74b4b749f06d", "ItemChest_03", 2, -250.0, 600.0, 40,
     None, True, [(0, "PalSphere", 20), (5, "PalUpgradeStone", 3)]),
    ("348547bb-3b45-aaef-4b20-518b143fbd4a", "Shelf04_Iron", 0, 120.0, -90.0, 20,
     "5145", True, [(0, "Arrow", 500)]),
    ("54f6254e-5940-4f46-f3e4-ce935e7a045b", "ItemChest_02", 1, 80.0, 110.0, 40,
     None, False, []),
    # Far from every base: must land unassigned rather than attached to one.
    ("9f1c7a22-0000-4000-8000-000000000005", "ItemChest", None, 400000.0, 400000.0, 40,
     None, False, [(0, "Paldium", 300)]),
]

# World loot, which must be classified as such and kept out of the chest lists.
LOOT = [
    ("aa000001-0000-4000-8000-000000000001", "TreasureBox", 2, 900.0, 900.0, 1,
     [(0, "Money", 796)]),
    ("aa000002-0000-4000-8000-000000000002", "TreasureBox_RequiredLongHold", None,
     250000.0, 100000.0, 1, [(0, "TreasureBoxKey01", 1)]),
]


def build() -> dict:
    containers: dict[str, dict] = {}
    objects = []

    def place(base_index, dx, dy):
        if base_index is None:
            return dx, dy, 0.0
        _, bx, by, bz = BASES[base_index]
        return bx + dx, by + dy, bz

    for guid, obj_type, base_index, dx, dy, capacity, code, lockable, slots in OBJECTS:
        x, y, z = place(base_index, dx, dy)
        containers[guid] = {
            "capacity": capacity,
            "slots": [
                {"slot_index": i, "item_id": item, "stack_count": n}
                for i, item, n in slots
            ],
        }
        objects.append({
            "object_type": obj_type, "container": guid,
            "x": x, "y": y, "z": z,
            "lockable": lockable, "lock_code": code,
        })

    for guid, obj_type, base_index, dx, dy, capacity, slots in LOOT:
        x, y, z = place(base_index, dx, dy)
        containers[guid] = {
            "capacity": capacity,
            "slots": [
                {"slot_index": i, "item_id": item, "stack_count": n}
                for i, item, n in slots
            ],
        }
        objects.append({
            "object_type": obj_type, "container": guid,
            "x": x, "y": y, "z": z, "lockable": False, "lock_code": None,
        })

    # A door: carries a code, owns no container. Dropping these would lose the
    # codes this app exists to track.
    bx, by, bz = BASES[2][1], BASES[2][2], BASES[2][3]
    objects.append({
        "object_type": "Stone_DoorWall", "container": None,
        "x": bx + 310.0, "y": by + 405.0, "z": bz,
        "lockable": True, "lock_code": "7826",
    })
    # A structure with neither a container nor a code: must be ignored entirely.
    objects.append({
        "object_type": "Wooden_Foundation", "container": None,
        "x": bx, "y": by, "z": bz, "lockable": False, "lock_code": None,
    })

    return {
        "note": "test fixture, not a real save",
        "bases": [
            {"base_guid": g, "x": x, "y": y, "z": z} for g, x, y, z in BASES
        ],
        "containers": containers,
        "objects": objects,
    }


if __name__ == "__main__":
    out = Path(__file__).with_name("world.json")
    out.write_text(json.dumps(build(), indent=1), encoding="utf-8")
    print(f"wrote {out}")
