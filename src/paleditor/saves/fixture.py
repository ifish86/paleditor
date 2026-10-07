"""A JSON-backed save backend for development and tests.

It reads a small, readable JSON world instead of a compressed save, so the test
suite needs neither the game, the Oodle library, nor a 31MB parse.

The schema mirrors the real format in the ways that matter, so tests exercise
the same semantics the live backend has:

  * ``capacity`` is the container's size; ``slots`` holds only occupied slots,
    exactly as the save does
  * an object may be lockable with no code set, which is different from an
    object that cannot be locked
  * an object may carry a lock code and own no container at all (a door)

    {
      "bases":   [{"base_guid": "...", "x": 0, "y": 0, "z": 0}],
      "containers": {"<guid>": {"capacity": 40,
                                "slots": [{"slot_index": 0,
                                           "item_id": "Wood",
                                           "stack_count": 99}]}},
      "objects": [{"object_type": "ItemChest_02", "container": "<guid>",
                   "x": 0, "y": 0, "z": 0,
                   "lockable": true, "lock_code": "1234"}]
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from ..errors import SaveFormatError
from . import fieldpaths as fp
from .base import sha256_file
from .types import ApplyReport, BaseRecord, ChestRecord, SlotEdit, SlotRecord, WorldSnapshot


class FixtureBackend:
    name = "fixture"

    def available(self) -> bool:
        return True

    def _load(self, level_sav: Path) -> dict:
        try:
            parsed = json.loads(level_sav.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SaveFormatError(
                f"{level_sav} is not valid JSON; the fixture backend reads JSON "
                f"worlds, not compressed saves: {exc}"
            ) from exc
        except OSError as exc:
            raise SaveFormatError(f"cannot read {level_sav}: {exc}") from exc
        if not isinstance(parsed, dict) or "objects" not in parsed:
            raise SaveFormatError(f"{level_sav} is not a paleditor fixture world")
        return parsed

    def read_snapshot(self, level_sav: Path) -> WorldSnapshot:
        world = self._load(level_sav)
        bases = tuple(
            BaseRecord(
                base_guid=b["base_guid"],
                name=b.get("name"),
                x=b.get("x"), y=b.get("y"), z=b.get("z"),
                guild_id=b.get("guild_id"),
            )
            for b in world.get("bases", [])
        )
        containers = world.get("containers", {})
        chests: list[ChestRecord] = []
        warnings: list[str] = []
        lock_only = 0

        for obj in world["objects"]:
            object_type = obj.get("object_type")
            kind = fp.classify(object_type)
            lock_code = obj.get("lock_code") or None
            lockable = bool(obj.get("lockable") or lock_code)
            guid = obj.get("container")
            if kind is None and not lock_code:
                continue
            if guid is None and lock_code:
                lock_only += 1

            info = containers.get(guid) if guid else None
            slots = tuple(
                SlotRecord(
                    slot_index=s["slot_index"],
                    item_id=s.get("item_id"),
                    stack_count=s.get("stack_count", 0),
                )
                for s in (info or {}).get("slots", [])
                if s.get("item_id") and s.get("stack_count", 0) > 0
            )
            chests.append(
                ChestRecord(
                    container_guid=guid or f"lock-{object_type}-{obj.get('x')}-{obj.get('y')}",
                    object_type=object_type,
                    x=obj.get("x"), y=obj.get("y"), z=obj.get("z"),
                    guild_id=obj.get("guild_id"),
                    lock_code=lock_code,
                    slot_count=(info or {}).get("capacity", 0),
                    slots=tuple(sorted(slots, key=lambda s: s.slot_index)),
                    kind=kind or "lock-only",
                    lockable=lockable,
                    has_container=guid is not None,
                )
            )

        chests = _assign(chests, bases)
        unassigned = sum(1 for c in chests if c.base_guid is None)
        if unassigned:
            warnings.append(f"{unassigned} chest(s) were not within range of any base camp")
        if lock_only:
            warnings.append(
                f"{lock_only} locked object(s) (doors and the like) carry a code "
                "but hold no items"
            )

        stat = level_sav.stat()
        return WorldSnapshot(
            bases=bases,
            chests=tuple(chests),
            backend=self.name,
            lock_codes_available=any(c.lock_code for c in chests),
            warnings=tuple(warnings),
            save_sha256=sha256_file(level_sav),
            save_mtime=stat.st_mtime,
        )

    def apply_edits(
        self, level_sav: Path, out_path: Path, edits: Sequence[SlotEdit]
    ) -> ApplyReport:
        world = self._load(level_sav)
        containers = world.get("containers", {})
        report = ApplyReport()

        for edit in edits:
            info = containers.get(edit.container_guid)
            if info is None:
                report.fail(edit.edit_id, f"container {edit.container_guid} not found in save")
                continue
            capacity = info.get("capacity", 0)
            if capacity and edit.slot_index >= capacity:
                report.fail(
                    edit.edit_id,
                    f"slot {edit.slot_index} is beyond this container's capacity "
                    f"of {capacity}",
                )
                continue
            slots = info.setdefault("slots", [])
            target = next(
                (s for s in slots if s.get("slot_index") == edit.slot_index), None
            )
            if edit.is_clear:
                if target is not None:
                    slots.remove(target)
            elif target is not None:
                target["item_id"] = edit.item_id
                target["stack_count"] = edit.stack_count
            else:
                # Only occupied slots are stored, so filling an empty one adds
                # an entry rather than editing one.
                slots.append(
                    {
                        "slot_index": edit.slot_index,
                        "item_id": edit.item_id,
                        "stack_count": edit.stack_count,
                    }
                )
                slots.sort(key=lambda s: s["slot_index"])
            report.applied.append(edit.edit_id)

        out_path.write_text(json.dumps(world, indent=1), encoding="utf-8")
        return report


def _assign(chests: list[ChestRecord], bases: tuple[BaseRecord, ...]) -> list[ChestRecord]:
    import math
    from dataclasses import replace

    if not bases:
        return chests
    out = []
    for chest in chests:
        if chest.x is None or chest.y is None:
            out.append(chest)
            continue
        best, best_distance = None, math.inf
        for base in bases:
            if base.x is None or base.y is None:
                continue
            distance = math.dist((chest.x, chest.y), (base.x, base.y))
            if distance < best_distance:
                best, best_distance = base, distance
        out.append(
            replace(
                chest,
                base_guid=best.base_guid
                if best and best_distance <= fp.BASE_ASSIGNMENT_RADIUS
                else None,
            )
        )
    return out
