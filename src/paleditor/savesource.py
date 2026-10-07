"""Choosing which Level.sav to read.

The dedicated server rewrites the live `Level.sav` roughly every 30 seconds. A
read landing mid-write returns a torn file: the header still declares the old
compressed length while the body is partly new. container.validate() catches
that, but the better answer is not to read the live file at all.

The server also writes a completed snapshot into `backup/world/<timestamp>/`
every save cycle. Those are closed files, so they are safe to read at any time.
Ingest prefers them; the maintenance window always uses the live file, because
by then the server is stopped and the live file is the one being edited.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .errors import SaveFormatError

log = logging.getLogger(__name__)

BACKUP_SUBPATH = ("backup", "world")


@dataclass(frozen=True)
class SaveSource:
    level_sav: Path
    # The directory the save was taken from, which also holds Players/.
    root: Path
    is_backup: bool
    label: str


def live(save_dir: Path) -> SaveSource:
    level = save_dir / "Level.sav"
    if not level.is_file():
        raise SaveFormatError(f"no Level.sav under {save_dir}")
    return SaveSource(level_sav=level, root=save_dir, is_backup=False, label="live")


def newest_backup(save_dir: Path) -> SaveSource | None:
    """The most recent completed snapshot, if there is one."""
    backups = save_dir.joinpath(*BACKUP_SUBPATH)
    if not backups.is_dir():
        return None
    snapshots = sorted(
        (d for d in backups.iterdir() if (d / "Level.sav").is_file()),
        key=lambda d: d.name,
    )
    if not snapshots:
        return None
    newest = snapshots[-1]
    return SaveSource(
        level_sav=newest / "Level.sav",
        root=newest,
        is_backup=True,
        label=newest.name,
    )


def pick(save_dir: Path, *, prefer_backup: bool = True) -> SaveSource:
    """Pick a save to read, preferring a completed snapshot."""
    if prefer_backup:
        snapshot = newest_backup(save_dir)
        if snapshot is not None:
            return snapshot
        log.warning(
            "no completed snapshot under %s; falling back to the live Level.sav, "
            "which the server may be writing to",
            save_dir.joinpath(*BACKUP_SUBPATH),
        )
    return live(save_dir)
