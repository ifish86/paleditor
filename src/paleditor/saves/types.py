"""Plain records the save backends produce and consume.

Nothing above this module knows what GVAS is. Ingest, the API and the write
path all speak in these records, which is what lets the parser be swapped
without touching anything else.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SlotRecord:
    slot_index: int
    item_id: str | None
    stack_count: int


@dataclass(frozen=True)
class ChestRecord:
    container_guid: str
    object_type: str | None = None
    x: float | None = None
    y: float | None = None
    z: float | None = None
    guild_id: str | None = None
    # None means "not found in this save", not "no code set". Callers must not
    # distinguish an unlocked chest from a parser that lost the field.
    lock_code: str | None = None
    # The container's capacity (SlotNum), not the number of stored slots. The
    # save records only occupied slots, so these differ for any chest that is
    # not completely full.
    slot_count: int = 0
    slots: tuple[SlotRecord, ...] = ()
    base_guid: str | None = None
    # "storage" for player-placed chests, "loot" for world treasure boxes,
    # "lock-only" for doors that carry a code but hold nothing.
    kind: str = "storage"
    # True when the object has a PasswordLock module at all. An object that can
    # be locked but has no code set is different from one that cannot be locked.
    lockable: bool = False
    has_container: bool = True


@dataclass(frozen=True)
class BaseRecord:
    base_guid: str
    name: str | None = None
    x: float | None = None
    y: float | None = None
    z: float | None = None
    guild_id: str | None = None


@dataclass(frozen=True)
class WorldSnapshot:
    """Everything one ingest pass needs out of a Level.sav."""

    bases: tuple[BaseRecord, ...]
    chests: tuple[ChestRecord, ...]
    backend: str
    # True when the backend located a lock-code field at all. False puts the
    # UI into contents-only mode rather than showing every chest as unlocked.
    lock_codes_available: bool = False
    warnings: tuple[str, ...] = ()
    save_sha256: str | None = None
    save_mtime: float | None = None


@dataclass(frozen=True)
class SlotEdit:
    """One queued change. ``item_id is None`` clears the slot."""

    edit_id: int
    container_guid: str
    slot_index: int
    item_id: str | None
    stack_count: int

    @property
    def is_clear(self) -> bool:
        return self.item_id is None or self.stack_count <= 0


@dataclass
class ApplyReport:
    """What the write step actually did, per edit."""

    applied: list[int] = field(default_factory=list)
    failed: dict[int, str] = field(default_factory=dict)

    def fail(self, edit_id: int, reason: str) -> None:
        self.failed[edit_id] = reason

    @property
    def any_applied(self) -> bool:
        return bool(self.applied)
