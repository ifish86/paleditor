"""The .sav container: header validation, decompression and compression.

A Palworld .sav is a 12-byte header followed by a compressed GVAS payload:

    u32 uncompressed_size | u32 compressed_size | 3-byte magic | u8 save_type

Two magics exist in the wild:

  PlZ  zlib. What palworld-save-tools handles, and what older saves used.
  PlM  Oodle Mermaid. What this server actually writes. Needs libooz.

paleditor reads both and writes PlZ, because no working Oodle *compressor* is
available: libooz exports OodLZ_Compress but segfaults when called. Whether the
game accepts a PlZ save in place of a PlM one is verified by
``paleditor check-write``, which must pass before the write path is enabled.
"""

from __future__ import annotations

import logging
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

from ..errors import SaveFormatError
from . import oodle

log = logging.getLogger(__name__)

HEADER_SIZE = 12
MAGIC_ZLIB = b"PlZ"
MAGIC_OODLE = b"PlM"
# 0x31 single-compressed, 0x32 double-compressed (zlib only).
TYPE_SINGLE = 0x31
TYPE_DOUBLE = 0x32


@dataclass(frozen=True)
class SaveContainer:
    gvas: bytes
    magic: bytes
    save_type: int

    @property
    def was_oodle(self) -> bool:
        return self.magic == MAGIC_OODLE


def inspect(raw: bytes) -> tuple[int, int, bytes, int]:
    """Read and sanity-check the header without decompressing."""
    if len(raw) < HEADER_SIZE:
        raise SaveFormatError(
            f"save is only {len(raw)} bytes, too short to hold a header"
        )
    uncompressed, compressed = struct.unpack_from("<II", raw, 0)
    magic = raw[8:11]
    save_type = raw[11]
    return uncompressed, compressed, magic, save_type


def validate(raw: bytes, *, path: Path | None = None) -> None:
    """Refuse a file that is not a complete save.

    This is the torn-read guard. The dedicated server rewrites Level.sav every
    ~30 seconds, and a read landing mid-write returns a file whose declared
    compressed length does not match what is actually there. Catching it here
    means ingest fails loudly instead of parsing a truncated world.
    """
    name = path.name if path else "save"
    uncompressed, compressed, magic, save_type = inspect(raw)

    if magic not in (MAGIC_ZLIB, MAGIC_OODLE):
        raise SaveFormatError(
            f"{name}: not a Palworld save, found magic {magic!r} "
            f"(expected {MAGIC_ZLIB!r} or {MAGIC_OODLE!r})"
        )
    if uncompressed <= 0 or compressed <= 0:
        raise SaveFormatError(
            f"{name}: header declares {uncompressed} uncompressed / "
            f"{compressed} compressed bytes; the file is empty or corrupt"
        )
    actual = len(raw) - HEADER_SIZE
    if magic == MAGIC_OODLE or save_type == TYPE_SINGLE:
        if compressed != actual:
            raise SaveFormatError(
                f"{name}: header declares {compressed} compressed bytes but the "
                f"file holds {actual}. The save is truncated, which usually means "
                "it was read while the server was writing it. Read a completed "
                "snapshot from backup/world/ instead."
            )


def read(raw: bytes, *, path: Path | None = None, oodle_library=None) -> SaveContainer:
    """Validate, decompress and return the GVAS payload."""
    validate(raw, path=path)
    uncompressed, compressed, magic, save_type = inspect(raw)
    payload = raw[HEADER_SIZE:]

    if magic == MAGIC_OODLE:
        library = oodle.load(oodle_library)
        gvas = library.decompress(payload, uncompressed)
    else:
        if save_type not in (TYPE_SINGLE, TYPE_DOUBLE):
            raise SaveFormatError(f"unknown zlib save type 0x{save_type:02x}")
        try:
            gvas = zlib.decompress(payload)
            if save_type == TYPE_DOUBLE:
                gvas = zlib.decompress(gvas)
        except zlib.error as exc:
            raise SaveFormatError(f"zlib decompression failed: {exc}") from exc

    if len(gvas) != uncompressed:
        raise SaveFormatError(
            f"decompressed to {len(gvas)} bytes but the header declared "
            f"{uncompressed}; refusing to use a partial world"
        )
    if gvas[:4] != b"GVAS":
        raise SaveFormatError(
            f"decompressed payload does not start with GVAS (got {gvas[:4]!r})"
        )
    return SaveContainer(gvas=gvas, magic=magic, save_type=save_type)


def write(gvas: bytes) -> bytes:
    """Wrap a GVAS payload in a PlZ (zlib) container.

    Always PlZ. There is no working Oodle compressor available, so a world read
    as PlM is written back as PlZ. That is a real container change, which is
    why ``paleditor check-write`` exists and why the maintenance window refuses
    to run until it has passed on this server.
    """
    if gvas[:4] != b"GVAS":
        raise SaveFormatError("refusing to compress a payload that is not GVAS")
    compressed = zlib.compress(gvas)
    return b"".join(
        (
            struct.pack("<II", len(gvas), len(compressed)),
            MAGIC_ZLIB,
            bytes([TYPE_SINGLE]),
            compressed,
        )
    )
