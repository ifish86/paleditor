"""Turn a parsed Level.sav dictionary into paleditor's plain records.

Shared by every backend: the cheahjs backend hands in what the parser
produced, the fixture backend hands in JSON read from disk. Keeping one
extractor means the test fixtures exercise the same code that runs in anger.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

from ..errors import SaveFormatError
from . import fieldpaths as fp
from .base import dig
from .types import BaseRecord, ChestRecord, SlotRecord, WorldSnapshot


def _world_save_data(parsed: dict) -> dict:
    node = dig(parsed, fp.WORLD_SAVE_DATA)
    if not isinstance(node, dict):
        # Some dumps are already rooted at worldSaveData.
        if isinstance(parsed, dict) and "MapObjectSaveData" in parsed:
            return parsed
        raise SaveFormatError(
            "could not find worldSaveData in the parsed save. The save layout "
            "does not match what paleditor expects, which usually means a game "
            "update changed it. Refusing to guess."
        )
    return node


def _first_hit(node: object, candidates: Iterable[tuple[str, ...]]) -> tuple[Any, tuple[str, ...] | None]:
    """Try each candidate path, returning the first value found and its path."""
    for path in candidates:
        value = dig(node, path)
        if value not in (None, ""):
            return value, path
    return None, None


def _as_guid(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("ID", "id", "value", "GUID", "guid"):
            if key in value:
                return _as_guid(value[key])
        return None
    text = str(value).strip()
    if not text or text in {"00000000-0000-0000-0000-000000000000", "None"}:
        return None
    return text.lower()


def _as_xyz(value: object) -> tuple[float | None, float | None, float | None]:
    if not isinstance(value, dict):
        return (None, None, None)
    out = []
    for key in ("x", "y", "z"):
        raw = value.get(key, value.get(key.upper()))
        try:
            out.append(float(raw)) if raw is not None else out.append(None)
        except (TypeError, ValueError):
            out.append(None)
    return (out[0], out[1], out[2])


def _looks_like_chest(object_type: str | None) -> bool:
    if not object_type:
        return False
    lowered = object_type.lower()
    if any(skip in lowered for skip in fp.CHEST_OBJECT_EXCLUDES):
        return False
    if object_type in fp.CHEST_OBJECT_TYPES:
        return True
    return any(hint in lowered for hint in fp.CHEST_NAME_HINTS)


def _extract_slots(container: object) -> tuple[SlotRecord, ...]:
    raw_slots = dig(container, fp.CONTAINER_SLOTS)
    if raw_slots is None and isinstance(container, dict):
        raw_slots = container.get("Slots")
    if not isinstance(raw_slots, list):
        return ()
    slots: list[SlotRecord] = []
    for index, raw in enumerate(raw_slots):
        slot_index = dig(raw, fp.SLOT_INDEX)
        if not isinstance(slot_index, int):
            slot_index = index
        item_id = dig(raw, fp.SLOT_ITEM_ID)
        if isinstance(item_id, str):
            item_id = item_id.strip() or None
        else:
            item_id = None
        stack = dig(raw, fp.SLOT_STACK_COUNT)
        stack = stack if isinstance(stack, int) else 0
        # An empty slot is recorded, not skipped: the UI renders a fixed grid
        # and an edit needs a slot index to target.
        if item_id is None or stack <= 0:
            slots.append(SlotRecord(slot_index=slot_index, item_id=None, stack_count=0))
        else:
            slots.append(SlotRecord(slot_index=slot_index, item_id=item_id, stack_count=stack))
    slots.sort(key=lambda s: s.slot_index)
    return tuple(slots)


def _extract_bases(world: dict) -> tuple[BaseRecord, ...]:
    node = dig(world, fp.BASE_CAMP_SAVE_DATA)
    if node is None:
        node = world.get("BaseCampSaveData")
    entries: list[tuple[str | None, dict]] = []
    if isinstance(node, dict):
        entries = [(key, value) for key, value in node.items() if isinstance(value, dict)]
    elif isinstance(node, list):
        entries = [(None, value) for value in node if isinstance(value, dict)]

    bases: list[BaseRecord] = []
    for key, entry in entries:
        inner = entry.get("value") if isinstance(entry.get("value"), dict) else entry
        guid = _as_guid(dig(inner, fp.BASE_CAMP_ID)) or _as_guid(key)
        if guid is None:
            continue
        x, y, z = _as_xyz(dig(inner, fp.BASE_CAMP_TRANSFORM))
        bases.append(
            BaseRecord(
                base_guid=guid,
                name=inner.get("name") if isinstance(inner.get("name"), str) else None,
                x=x, y=y, z=z,
                guild_id=_as_guid(dig(inner, fp.BASE_CAMP_GUILD_ID)),
            )
        )
    return tuple(bases)


def _extract_chests(world: dict) -> tuple[list[ChestRecord], bool, list[str]]:
    objects = dig(world, fp.MAP_OBJECT_SAVE_DATA)
    if objects is None:
        objects = world.get("MapObjectSaveData")
    if not isinstance(objects, list):
        raise SaveFormatError(
            "MapObjectSaveData is missing or not a list; refusing to ingest a "
            "save whose object table paleditor cannot read."
        )

    containers = dig(world, fp.ITEM_CONTAINER_SAVE_DATA)
    if containers is None:
        containers = world.get("ItemContainerSaveData")
    container_map: dict[str, Any] = {}
    if isinstance(containers, dict):
        for key, value in containers.items():
            guid = _as_guid(key) or _as_guid(dig(value, ("key",)))
            if guid:
                container_map[guid] = value.get("value", value) if isinstance(value, dict) else value
    elif isinstance(containers, list):
        for entry in containers:
            guid = _as_guid(dig(entry, ("key", "ID", "value"))) or _as_guid(dig(entry, ("key",)))
            if guid:
                container_map[guid] = dig(entry, ("value",)) or entry

    warnings: list[str] = []
    lock_hit_path: tuple[str, ...] | None = None
    chests: list[ChestRecord] = []
    missing_containers = 0

    for entry in objects:
        if not isinstance(entry, dict):
            continue
        object_type = dig(entry, fp.MAP_OBJECT_ID)
        if isinstance(object_type, dict):
            object_type = _as_guid(object_type)
        object_type = object_type if isinstance(object_type, str) else None
        if not _looks_like_chest(object_type):
            continue

        container_guid, _ = _first_hit(entry, fp.CONTAINER_ID_CANDIDATES)
        container_guid = _as_guid(container_guid)
        if container_guid is None:
            missing_containers += 1
            continue

        lock_code, hit_path = _first_hit(entry, fp.LOCK_CODE_CANDIDATES)
        if hit_path is not None:
            lock_hit_path = hit_path
            lock_code = str(lock_code).strip() or None
        else:
            lock_code = None

        x, y, z = _as_xyz(dig(entry, fp.MAP_OBJECT_TRANSFORM))
        slots = _extract_slots(container_map.get(container_guid))
        chests.append(
            ChestRecord(
                container_guid=container_guid,
                object_type=object_type,
                x=x, y=y, z=z,
                guild_id=_as_guid(dig(entry, fp.MAP_OBJECT_GUILD_ID)),
                lock_code=lock_code,
                slot_count=len(slots),
                slots=slots,
            )
        )

    if missing_containers:
        warnings.append(
            f"{missing_containers} chest-like object(s) had no container GUID and "
            "were skipped"
        )
    if lock_hit_path is None and chests:
        warnings.append(
            "no lock code field was found on any chest; running in "
            "contents-only mode (see docs/save-format.md)"
        )
    return chests, lock_hit_path is not None, warnings


def _distance(a: ChestRecord, b: BaseRecord) -> float:
    if None in (a.x, a.y, b.x, b.y):
        return math.inf
    dz = (a.z or 0.0) - (b.z or 0.0)
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + dz**2)


def assign_bases(
    chests: list[ChestRecord], bases: tuple[BaseRecord, ...]
) -> list[ChestRecord]:
    """Group chests by nearest base camp, guild id as tiebreaker.

    Map objects carry no parent base link, so this is inference, not data. A
    chest further than BASE_ASSIGNMENT_RADIUS from every base is left
    unassigned rather than attached to something it does not belong to.
    """
    if not bases:
        return chests
    out: list[ChestRecord] = []
    for chest in chests:
        same_guild = [b for b in bases if chest.guild_id and b.guild_id == chest.guild_id]
        pool = same_guild or list(bases)
        best = min(pool, key=lambda b: _distance(chest, b))
        distance = _distance(chest, best)
        base_guid = best.base_guid if distance <= fp.BASE_ASSIGNMENT_RADIUS else None
        out.append(
            ChestRecord(
                container_guid=chest.container_guid,
                object_type=chest.object_type,
                x=chest.x, y=chest.y, z=chest.z,
                guild_id=chest.guild_id,
                lock_code=chest.lock_code,
                slot_count=chest.slot_count,
                slots=chest.slots,
                base_guid=base_guid,
            )
        )
    return out


def snapshot_from_parsed(parsed: dict, *, backend: str) -> WorldSnapshot:
    """The one entry point every backend uses."""
    world = _world_save_data(parsed)
    bases = _extract_bases(world)
    chests, lock_available, warnings = _extract_chests(world)
    chests = assign_bases(chests, bases)
    unassigned = sum(1 for c in chests if c.base_guid is None)
    if unassigned:
        warnings.append(f"{unassigned} chest(s) were not within range of any base camp")
    return WorldSnapshot(
        bases=bases,
        chests=tuple(chests),
        backend=backend,
        lock_codes_available=lock_available,
        warnings=tuple(warnings),
    )
