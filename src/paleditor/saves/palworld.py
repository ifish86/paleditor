"""The real save backend: reads this server's PlM saves, writes PlZ.

Structure comes from palworld-save-tools with its custom decoders disabled, and
the few RawData blobs paleditor needs are decoded in blobs.py. That split is
deliberate: the outer GVAS layout is stable and size-delimited, so a property
paleditor does not understand is skipped by its declared size, while the record
decoders that broke on this world are never invoked.
"""

from __future__ import annotations

import contextlib
import io
import logging
import math
from dataclasses import replace
from pathlib import Path
from typing import Any, Sequence

from ..errors import ParserUnavailable, SaveFormatError
from . import blobs, container
from . import fieldpaths as fp
from .base import sha256_file
from .types import ApplyReport, BaseRecord, ChestRecord, SlotEdit, SlotRecord, WorldSnapshot

log = logging.getLogger(__name__)

_IMPORT_HINT = (
    "palworld-save-tools is not installed. Install the parser extra:\n"
    "    pip install -e '.[parser]'"
)


def _gvas_module():
    try:
        from palworld_save_tools.gvas import GvasFile
        from palworld_save_tools.paltypes import PALWORLD_TYPE_HINTS
        from palworld_save_tools.archive import UUID
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ParserUnavailable(_IMPORT_HINT) from exc
    return GvasFile, PALWORLD_TYPE_HINTS, UUID


def _raw(node: Any) -> bytes | None:
    """Pull the bytes out of a RawData ArrayProperty."""
    if not isinstance(node, dict):
        return None
    values = node.get("RawData", {}).get("value", {}).get("values")
    if values is None:
        return None
    return bytes(values)


def _set_raw(node: dict, payload: bytes) -> None:
    node["RawData"]["value"]["values"] = list(payload)


def _module_map(obj: dict) -> list:
    return (
        obj.get("ConcreteModel", {})
        .get("value", {})
        .get("ModuleMap", {})
        .get("value", [])
    )


def _module(obj: dict, key: str) -> dict | None:
    for entry in _module_map(obj):
        if entry.get("key") == key:
            value = entry.get("value")
            return value if isinstance(value, dict) else None
    return None


class PalworldBackend:
    """SaveBackend over the real format."""

    name = "palworld"

    def __init__(self, oodle_library: str | None = None):
        self.oodle_library = oodle_library

    def available(self) -> bool:
        try:
            _gvas_module()
        except ParserUnavailable:
            return False
        return True

    # -- loading -----------------------------------------------------------

    def _load(self, level_sav: Path):
        GvasFile, hints, UUID = _gvas_module()
        try:
            raw = level_sav.read_bytes()
        except OSError as exc:
            raise SaveFormatError(f"cannot read {level_sav}: {exc}") from exc
        box = container.read(raw, path=level_sav, oodle_library=self.oodle_library)
        try:
            # The reader prints "Struct type ... not found" lines to stdout for
            # every unmapped struct. They are expected with decoders off and
            # would otherwise flood the service log.
            with contextlib.redirect_stdout(io.StringIO()):
                gvas = GvasFile.read(box.gvas, hints, {})
        except Exception as exc:
            raise SaveFormatError(
                f"could not parse the GVAS structure of {level_sav.name}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        world = gvas.properties.get("worldSaveData", {}).get("value")
        if not isinstance(world, dict):
            raise SaveFormatError("worldSaveData is missing from the parsed save")
        return gvas, world, box, UUID

    # -- reading -----------------------------------------------------------

    def read_snapshot(self, level_sav: Path) -> WorldSnapshot:
        gvas, world, box, UUID = self._load(level_sav)
        warnings: list[str] = []
        if box.was_oodle:
            warnings.append(
                "this world uses the Oodle (PlM) container; paleditor can read it "
                "but writes the zlib (PlZ) container"
            )

        containers = self._read_containers(world, UUID)
        bases = self._read_bases(world)
        chests, lock_warnings = self._read_objects(world, containers, UUID)
        warnings.extend(lock_warnings)

        chests = _assign_bases(chests, bases)
        unassigned = sum(1 for c in chests if c.base_guid is None)
        if unassigned:
            warnings.append(f"{unassigned} chest(s) were not within range of any base camp")

        stat = level_sav.stat()
        return WorldSnapshot(
            bases=tuple(bases),
            chests=tuple(chests),
            backend=self.name,
            lock_codes_available=any(c.lock_code for c in chests),
            warnings=tuple(warnings),
            save_sha256=sha256_file(level_sav),
            save_mtime=stat.st_mtime,
        )

    def _read_containers(self, world: dict, UUID) -> dict[str, dict]:
        """container guid -> {'capacity': int, 'slots': [SlotRecord], 'node': entry}"""
        entries = world.get(fp.ITEM_CONTAINER_KEY, {}).get("value")
        if not isinstance(entries, list):
            raise SaveFormatError(
                f"{fp.ITEM_CONTAINER_KEY} is missing or not a list; refusing to "
                "ingest a save whose container table paleditor cannot read"
            )
        out: dict[str, dict] = {}
        for entry in entries:
            key = entry.get("key", {}).get("ID", {}).get("value")
            if key is None:
                continue
            guid = str(key)
            value = entry.get("value", {})
            capacity = value.get("SlotNum", {}).get("value")
            slots: list[SlotRecord] = []
            for node in value.get("Slots", {}).get("value", {}).get("values") or []:
                payload = _raw(node)
                if payload is None:
                    continue
                try:
                    slot = blobs.decode_slot(payload)
                except SaveFormatError:
                    continue
                if slot.is_empty:
                    continue
                slots.append(
                    SlotRecord(
                        slot_index=slot.slot_index,
                        item_id=slot.item_id,
                        stack_count=slot.stack_count,
                    )
                )
            slots.sort(key=lambda s: s.slot_index)
            out[guid] = {
                "capacity": capacity if isinstance(capacity, int) and capacity > 0
                            else (max((s.slot_index for s in slots), default=-1) + 1),
                "slots": slots,
                "entry": entry,
            }
        return out

    def _read_bases(self, world: dict) -> list[BaseRecord]:
        node = world.get(fp.BASE_CAMP_KEY, {}).get("value")
        bases: list[BaseRecord] = []
        entries = node if isinstance(node, list) else []
        for entry in entries:
            key = entry.get("key")
            value = entry.get("value", {})
            guid = str(key) if key is not None else None
            if guid is None:
                continue
            # Like map objects, the base camp's position lives in its RawData
            # rather than as a property.
            position = blobs.decode_transform(_raw(value) or b"")
            x, y, z = position if position else (None, None, None)
            bases.append(
                BaseRecord(base_guid=guid, name=None, x=x, y=y, z=z, guild_id=None)
            )
        return bases

    def _read_objects(self, world: dict, containers: dict, UUID):
        objects = (
            world.get(fp.MAP_OBJECT_KEY, {}).get("value", {}).get("values")
        )
        if not isinstance(objects, list):
            raise SaveFormatError(
                "MapObjectSaveData is missing or not a list; refusing to ingest"
            )
        chests: list[ChestRecord] = []
        warnings: list[str] = []
        lockable_without_container = 0

        for obj in objects:
            if not isinstance(obj, dict):
                continue
            object_type = obj.get("MapObjectId", {}).get("value")
            if not isinstance(object_type, str):
                continue

            lock_module = _module(obj, fp.MODULE_PASSWORD_LOCK)
            lock_code = None
            lockable = lock_module is not None
            if lock_module is not None:
                payload = _raw(lock_module)
                if payload:
                    try:
                        lock_code = blobs.decode_password_lock(payload).code or None
                    except SaveFormatError:
                        lock_code = None

            container_guid = None
            item_module = _module(obj, fp.MODULE_ITEM_CONTAINER)
            if item_module is not None:
                payload = _raw(item_module)
                if payload:
                    raw_id = blobs.container_id_bytes(payload)
                    if raw_id is not None:
                        container_guid = str(UUID(raw_id))

            kind = fp.classify(object_type)
            # A lockable object with no container is still worth keeping: door
            # locks share the chest codes, and lock codes are what this app is
            # for. It is recorded with no slots rather than dropped.
            if kind is None and not (lockable and lock_code):
                continue
            if container_guid is None and lock_code:
                lockable_without_container += 1

            info = containers.get(container_guid) if container_guid else None
            slots = tuple(info["slots"]) if info else ()
            capacity = info["capacity"] if info else 0
            x, y, z = _location(obj)

            chests.append(
                ChestRecord(
                    container_guid=container_guid or _synthetic_id(object_type, x, y, z),
                    object_type=object_type,
                    x=x, y=y, z=z,
                    guild_id=None,
                    lock_code=lock_code,
                    slot_count=capacity,
                    slots=slots,
                    kind=kind or "lock-only",
                    lockable=lockable,
                    has_container=container_guid is not None,
                )
            )

        if lockable_without_container:
            warnings.append(
                f"{lockable_without_container} locked object(s) (doors and the like) "
                "carry a code but hold no items"
            )
        return chests, warnings

    # -- writing -----------------------------------------------------------

    def apply_edits(
        self, level_sav: Path, out_path: Path, edits: Sequence[SlotEdit]
    ) -> ApplyReport:
        gvas, world, box, UUID = self._load(level_sav)
        containers = self._read_containers(world, UUID)
        report = ApplyReport()

        for edit in edits:
            info = containers.get(edit.container_guid)
            if info is None:
                report.fail(edit.edit_id, f"container {edit.container_guid} not found in save")
                continue
            try:
                self._apply_one(info, edit)
            except SaveFormatError as exc:
                report.fail(edit.edit_id, str(exc))
                continue
            report.applied.append(edit.edit_id)

        payload = container.write(gvas.write({}))
        out_path.write_bytes(payload)
        return report

    def _apply_one(self, info: dict, edit: SlotEdit) -> None:
        value = info["entry"].get("value", {})
        slot_nodes = value.get("Slots", {}).get("value", {}).get("values")
        if slot_nodes is None:
            raise SaveFormatError("container has no slot array")

        capacity = info["capacity"]
        if capacity and edit.slot_index >= capacity:
            raise SaveFormatError(
                f"slot {edit.slot_index} is beyond this container's capacity of {capacity}"
            )

        target = None
        for node in slot_nodes:
            payload = _raw(node)
            if payload is None:
                continue
            if blobs.decode_slot(payload).slot_index == edit.slot_index:
                target = node
                break

        if edit.is_clear:
            if target is None:
                # Already empty. Not an error: the end state the user asked for
                # is the state the save is in.
                return
            slot_nodes.remove(target)
            return

        if target is not None:
            existing = blobs.decode_slot(_raw(target))
            _set_raw(
                target,
                blobs.encode_slot(
                    replace(existing, item_id=edit.item_id, stack_count=edit.stack_count)
                ),
            )
            return

        # The slot is not stored, because the save holds only occupied slots.
        # Add one, copying the trailing bytes from a sibling so paleditor never
        # invents item data it does not understand.
        template = None
        for node in slot_nodes:
            payload = _raw(node)
            if payload is not None:
                template = node
                break
        if template is None:
            raise SaveFormatError(
                "cannot add an item to an empty container: there is no existing "
                "slot to copy the item record layout from"
            )
        tail = blobs.decode_slot(_raw(template)).tail
        import copy

        node = copy.deepcopy(template)
        _set_raw(
            node,
            blobs.new_slot_blob(edit.slot_index, edit.item_id, edit.stack_count, tail=tail),
        )
        slot_nodes.append(node)


# -- helpers ----------------------------------------------------------------


def _f(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _opt_str(value) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _location(obj: dict) -> tuple[float | None, float | None, float | None]:
    payload = _raw(obj.get("Model", {}).get("value", {}))
    if payload:
        found = blobs.decode_transform(payload)
        if found is not None:
            return found
    return (None, None, None)


def _synthetic_id(object_type: str, x, y, z) -> str:
    """A stable key for a lockable object that owns no container.

    Doors carry the same codes as the chests beside them, and lock codes are
    what this app exists for, so they are kept. They have no container GUID, so
    one is derived from type and position; it is stable as long as the object
    does not move.
    """
    import hashlib

    seed = f"{object_type}|{x:.1f}|{y:.1f}|{z:.1f}" if x is not None else object_type
    return "lock-" + hashlib.sha256(seed.encode()).hexdigest()[:24]


def _assign_bases(chests: list[ChestRecord], bases: list[BaseRecord]) -> list[ChestRecord]:
    """Nearest base camp within the radius, measured in 2D.

    Horizontal distance only: bases sit on terrain and a chest on an upper floor
    is still at that base.
    """
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
                base_guid=best.base_guid if best and best_distance <= fp.BASE_ASSIGNMENT_RADIUS else None,
            )
        )
    return out
