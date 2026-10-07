"""A JSON-backed save backend, for development and tests.

Reads a GVAS-shaped JSON dump instead of a compressed Level.sav. It runs the
same extractor as the real backend, so a fixture exercises the real field-path
walking rather than a parallel code path.

Pointing ``save_backend = "fixture"`` at a directory containing a Level.sav
that is really JSON lets the whole app, including the write path, be driven
end to end without the game installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from ..errors import SaveFormatError
from . import fieldpaths as fp
from .base import dig, sha256_file
from .extract import snapshot_from_parsed
from .types import ApplyReport, SlotEdit, WorldSnapshot


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
                f"dumps, not compressed saves: {exc}"
            ) from exc
        except OSError as exc:
            raise SaveFormatError(f"cannot read {level_sav}: {exc}") from exc
        if not isinstance(parsed, dict):
            raise SaveFormatError(f"{level_sav} must contain a JSON object")
        return parsed

    def read_snapshot(self, level_sav: Path) -> WorldSnapshot:
        parsed = self._load(level_sav)
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

    def apply_edits(
        self, level_sav: Path, out_path: Path, edits: Sequence[SlotEdit]
    ) -> ApplyReport:
        parsed = self._load(level_sav)
        world = dig(parsed, fp.WORLD_SAVE_DATA)
        if not isinstance(world, dict):
            world = parsed
        containers = dig(world, fp.ITEM_CONTAINER_SAVE_DATA)
        if containers is None:
            containers = world.get("ItemContainerSaveData")
        report = ApplyReport()

        for edit in edits:
            container = None
            if isinstance(containers, dict):
                for key, value in containers.items():
                    if str(key).lower() == edit.container_guid.lower():
                        container = value.get("value", value) if isinstance(value, dict) else value
                        break
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
                    f"{edit.container_guid} (slot count {len(slots)})",
                )
                continue
            _write_slot(target, edit)
            report.applied.append(edit.edit_id)

        out_path.write_text(json.dumps(parsed, indent=1), encoding="utf-8")
        return report


def _write_slot(slot: dict, edit: SlotEdit) -> None:
    """Set or clear one slot in a GVAS-shaped slot dict."""
    item = slot.setdefault("ItemId", {}).setdefault("value", {})
    static = item.setdefault("StaticId", {})
    count = slot.setdefault("StackCount", {})
    if edit.is_clear:
        static["value"] = "None"
        count["value"] = 0
    else:
        static["value"] = edit.item_id
        count["value"] = edit.stack_count
