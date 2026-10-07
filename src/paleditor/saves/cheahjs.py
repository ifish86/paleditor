"""The real save backend, over cheahjs' palworld-save-tools.

Install with the 'parser' extra. The import is deferred and guarded so the app,
the API and the test suite all work without it; only ingest and the write path
need it.

UNVERIFIED: the calls into palworld_save_tools below follow its documented
v0.24.0 API but have not been run against a live Level.sav from this project.
Phase 1 of the proposal is exactly that check, and
``paleditor verify-save --save-dir ...`` is the command for it. Until it passes
on the real world, treat this backend as unproven and leave save_backend
pointed at it only on the VPS, where the save actually is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from ..errors import ParserUnavailable, SaveFormatError
from . import fieldpaths as fp
from .base import dig, sha256_file
from .extract import snapshot_from_parsed
from .fixture import _write_slot
from .types import ApplyReport, SlotEdit, WorldSnapshot

_IMPORT_HINT = (
    "palworld-save-tools is not installed. Install the parser extra:\n"
    "    pip install -e '.[parser]'\n"
    "Ingest and the write path need it; the rest of the app does not."
)


def _imports() -> tuple[Any, Any, Any, Any, Any]:
    try:
        from palworld_save_tools.gvas import GvasFile
        from palworld_save_tools.palsav import (
            compress_gvas_to_sav,
            decompress_sav_to_gvas,
        )
        from palworld_save_tools.paltypes import (
            PALWORLD_CUSTOM_PROPERTIES,
            PALWORLD_TYPE_HINTS,
        )
    except ImportError as exc:  # pragma: no cover - depends on optional dep
        raise ParserUnavailable(_IMPORT_HINT) from exc
    return (
        GvasFile,
        compress_gvas_to_sav,
        decompress_sav_to_gvas,
        PALWORLD_CUSTOM_PROPERTIES,
        PALWORLD_TYPE_HINTS,
    )


class CheahjsBackend:
    name = "cheahjs"

    def available(self) -> bool:
        try:
            _imports()
        except ParserUnavailable:
            return False
        return True

    # -- reading ------------------------------------------------------------

    def _read_gvas(self, level_sav: Path) -> tuple[Any, Any, Any]:
        GvasFile, _, decompress, custom_props, type_hints = _imports()
        try:
            raw = level_sav.read_bytes()
        except OSError as exc:
            raise SaveFormatError(f"cannot read {level_sav}: {exc}") from exc
        try:
            raw_gvas, save_type = decompress(raw)
            gvas = GvasFile.read(raw_gvas, type_hints, custom_props)
        except Exception as exc:  # the parser raises a wide variety
            raise SaveFormatError(
                f"palworld-save-tools could not parse {level_sav.name}: "
                f"{type(exc).__name__}: {exc}. This usually means a game update "
                "changed the save format. Ingest refuses to continue rather "
                "than write a partial world."
            ) from exc
        return gvas, save_type, custom_props

    def read_snapshot(self, level_sav: Path) -> WorldSnapshot:
        gvas, _, _ = self._read_gvas(level_sav)
        parsed = gvas.dump()
        if not isinstance(parsed, dict):
            raise SaveFormatError("parser returned a non-dict save structure")
        snap = snapshot_from_parsed(parsed, backend=self.name)
        stat = level_sav.stat()
        return WorldSnapshot(
            bases=snap.bases,
            chests=snap.chests,
            backend=snap.backend,
            lock_codes_available=snap.lock_codes_available,
            warnings=snap.warnings,
            save_sha256=sha256_file(level_sav),
            save_mtime=stat.st_mtime,
        )

    # -- writing ------------------------------------------------------------

    def apply_edits(
        self, level_sav: Path, out_path: Path, edits: Sequence[SlotEdit]
    ) -> ApplyReport:
        """Apply edits in memory and serialise to ``out_path``.

        Never writes ``level_sav``. The window worker renames ``out_path`` into
        place once this returns and the batch has been verified.
        """
        _, compress, _, custom_props, _ = _imports()
        gvas, save_type, _ = self._read_gvas(level_sav)
        parsed = gvas.properties  # mutate the live structure, not a dump copy
        world = dig(parsed, ("worldSaveData", "value"))
        if not isinstance(world, dict):
            raise SaveFormatError("worldSaveData missing; refusing to write")
        containers = dig(world, fp.ITEM_CONTAINER_SAVE_DATA)
        report = ApplyReport()

        index = _index_containers(containers)
        for edit in edits:
            container = index.get(edit.container_guid.lower())
            if container is None:
                report.fail(edit.edit_id, f"container {edit.container_guid} not found in save")
                continue
            slots = dig(container, fp.CONTAINER_SLOTS)
            if not isinstance(slots, list):
                report.fail(edit.edit_id, "container has no readable slot array")
                continue
            target = next(
                (s for s in slots if dig(s, fp.SLOT_INDEX) == edit.slot_index), None
            )
            if target is None:
                report.fail(
                    edit.edit_id,
                    f"slot {edit.slot_index} does not exist in container "
                    f"{edit.container_guid}",
                )
                continue
            _write_slot(target, edit)
            report.applied.append(edit.edit_id)

        try:
            payload = compress(gvas.write(custom_props), save_type)
        except Exception as exc:
            raise SaveFormatError(
                f"serialising the edited save failed: {type(exc).__name__}: {exc}. "
                "Nothing was written; the original save is untouched."
            ) from exc
        out_path.write_bytes(payload)
        return report


def _index_containers(containers: object) -> dict[str, Any]:
    index: dict[str, Any] = {}
    if isinstance(containers, dict):
        for key, value in containers.items():
            index[str(key).lower()] = value.get("value", value) if isinstance(value, dict) else value
    elif isinstance(containers, list):
        for entry in containers:
            guid = dig(entry, ("key", "ID", "value")) or dig(entry, ("key",))
            if guid is not None:
                index[str(guid).lower()] = dig(entry, ("value",)) or entry
    return index
