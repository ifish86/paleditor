"""Codecs for the RawData byte blobs inside a Palworld save.

paleditor parses the save's outer GVAS structure with palworld-save-tools, but
with its custom property decoders turned OFF. Those decoders walk record
layouts, and this world's layout has already moved far enough that they raise
"EOF not reached" on the character records. With them off, every RawData field
arrives as opaque bytes and the surrounding structure parses cleanly, so only
the handful of blobs paleditor actually needs are decoded here.

Each layout below was read back from a live save on 2026-10-07 and is marked
CONFIRMED in fieldpaths.py. They are small, fixed-prefix structures, and each
decoder preserves whatever trailing bytes it does not understand so a re-encode
is byte-exact apart from the fields deliberately changed.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ..errors import SaveFormatError

# Slot blob:  i32 slot_index | i32 stack_count | i32 strlen | utf8+NUL | tail
# The tail is 52 bytes for 11,209 of 11,237 slots in the reference world, but a
# few carry more, so it is preserved verbatim rather than assumed.
SLOT_HEADER = struct.Struct("<iii")

# PasswordLock blob:  u8 version | i32 strlen | utf8+NUL | tail
# strlen == 0 means the object can be locked but no code is set, which is a
# different thing from an object that cannot be locked at all.
LOCK_PREFIX = struct.Struct("<B")
LOCK_LEN = struct.Struct("<i")

CONTAINER_ID_BYTES = 16


def _read_fstring(data: bytes, offset: int) -> tuple[str, int]:
    """Read a length-prefixed, NUL-terminated UE string."""
    if offset + 4 > len(data):
        raise SaveFormatError("string length runs past the end of the blob")
    length = struct.unpack_from("<i", data, offset)[0]
    offset += 4
    if length == 0:
        return "", offset
    if length < 0:  # UTF-16, negative count of code units
        size = -length * 2
        if offset + size > len(data):
            raise SaveFormatError("utf-16 string runs past the end of the blob")
        return data[offset : offset + size - 2].decode("utf-16-le", "replace"), offset + size
    if offset + length > len(data):
        raise SaveFormatError("utf-8 string runs past the end of the blob")
    return data[offset : offset + length - 1].decode("utf-8", "replace"), offset + length


def _write_fstring(text: str) -> bytes:
    if not text:
        return struct.pack("<i", 0)
    encoded = text.encode("utf-8") + b"\x00"
    return struct.pack("<i", len(encoded)) + encoded


# -- item slots -------------------------------------------------------------


@dataclass(frozen=True)
class Slot:
    slot_index: int
    stack_count: int
    item_id: str
    tail: bytes = b""

    @property
    def is_empty(self) -> bool:
        return not self.item_id or self.stack_count <= 0


def decode_slot(blob: bytes) -> Slot:
    if len(blob) < SLOT_HEADER.size:
        raise SaveFormatError(f"slot blob is only {len(blob)} bytes")
    slot_index, stack_count = struct.unpack_from("<ii", blob, 0)
    item_id, offset = _read_fstring(blob, 8)
    return Slot(
        slot_index=slot_index,
        stack_count=stack_count,
        item_id=item_id,
        tail=blob[offset:],
    )


def encode_slot(slot: Slot) -> bytes:
    return b"".join(
        (
            struct.pack("<ii", slot.slot_index, slot.stack_count),
            _write_fstring(slot.item_id),
            slot.tail,
        )
    )


def new_slot_blob(slot_index: int, item_id: str, stack_count: int, *, tail: bytes) -> bytes:
    """Build a slot that was not previously stored.

    The save records only occupied slots, so putting an item into an empty slot
    means adding an entry rather than editing one. The tail is copied from a
    sibling slot in the same container, because paleditor does not know what
    those bytes mean and must not invent them.
    """
    return encode_slot(
        Slot(slot_index=slot_index, stack_count=stack_count, item_id=item_id, tail=tail)
    )


# -- password locks ---------------------------------------------------------


@dataclass(frozen=True)
class PasswordLock:
    code: str
    version: int
    tail: bytes = b""

    @property
    def is_set(self) -> bool:
        return bool(self.code)


def decode_password_lock(blob: bytes) -> PasswordLock:
    if len(blob) < 5:
        raise SaveFormatError(f"password lock blob is only {len(blob)} bytes")
    version = blob[0]
    code, offset = _read_fstring(blob, 1)
    return PasswordLock(code=code, version=version, tail=blob[offset:])


def encode_password_lock(lock: PasswordLock) -> bytes:
    return bytes([lock.version]) + _write_fstring(lock.code) + lock.tail


# -- container ids ----------------------------------------------------------


def container_id_bytes(blob: bytes) -> bytes | None:
    """The 16 raw GUID bytes at the front of an ItemContainer module blob."""
    if len(blob) < CONTAINER_ID_BYTES:
        return None
    raw = blob[:CONTAINER_ID_BYTES]
    if raw == b"\x00" * CONTAINER_ID_BYTES:
        return None
    return raw


# -- transforms -------------------------------------------------------------

# The map object's world transform is inside the Model RawData, not exposed as
# a property. It is stored as rotation, then translation (3 doubles), then
# scale. Scale is (1,1,1) for every placed object observed, so the translation
# is located by scanning for that scale vector and stepping back 24 bytes —
# the same anchor palstats settled on, and it survives changes to the header
# in front of it.
_SCALE_ONE = struct.Struct("<3d").pack(1.0, 1.0, 1.0)
_XYZ = struct.Struct("<3d")

# Palworld's world is roughly +/-1,000,000 units. Anything outside this is a
# false positive from the scan rather than a real position.
WORLD_LIMIT = 2_000_000.0


def decode_transform(blob: bytes) -> tuple[float, float, float] | None:
    """Best-effort world position from a Model or BaseCamp RawData blob.

    Scans FORWARD and takes the first plausible hit. A base camp blob carries a
    second (1,1,1) later on, preceded by what looks like a bounding-box extent
    of (-170, 0, 170); reading backwards would return that for every base and
    put all three at the same place.
    """
    if not blob:
        return None
    position = blob.find(_SCALE_ONE)
    while position >= _XYZ.size:
        x, y, z = _XYZ.unpack_from(blob, position - _XYZ.size)
        if all(abs(v) <= WORLD_LIMIT and v == v for v in (x, y, z)):
            return (x, y, z)
        position = blob.find(_SCALE_ONE, position + 1)
    return None
