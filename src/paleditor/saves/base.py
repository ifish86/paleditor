"""The save backend interface.

cheahjs' palworld-save-tools last shipped v0.24.0 in October 2024, so the
parser is treated as replaceable from the start. Anything that reads or writes
a save goes through this protocol and nothing else.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

from .types import ApplyReport, SlotEdit, WorldSnapshot


@runtime_checkable
class SaveBackend(Protocol):
    name: str

    def available(self) -> bool:
        """True when this backend's dependencies are importable."""

    def read_snapshot(self, level_sav: Path) -> WorldSnapshot:
        """Parse a save into plain records.

        Must raise SaveFormatError when the structure does not match
        expectations, rather than returning a partial snapshot. Ingest relies
        on that to refuse to run after a game update changes the layout.
        """

    def apply_edits(
        self,
        level_sav: Path,
        out_path: Path,
        edits: Sequence[SlotEdit],
    ) -> ApplyReport:
        """Write a new save with ``edits`` applied.

        Must write to ``out_path`` and never modify ``level_sav`` in place; the
        window worker does the atomic rename itself.
        """


def sha256_file(path: Path, *, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def dig(node: object, path: Sequence[str]) -> object | None:
    """Walk a nested dict by key path, returning None on any miss.

    The parsed save is deeply nested and inconsistently populated, so every
    field read goes through this rather than chained subscripts.
    """
    current = node
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current
