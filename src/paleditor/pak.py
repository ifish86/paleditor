"""A minimal reader for Unreal .pak archives.

Enough to find one file and get its bytes out: the footer, the directory
index, the bit-packed entry encoding, and Oodle-compressed blocks. It is not a
general extractor and does not try to be - paleditor wants two data tables out
of a four-gigabyte archive, not the archive.

Palworld's server pak is version 11, unencrypted, Oodle-compressed, which is
the only combination handled here. Anything else raises rather than guessing.
"""

from __future__ import annotations

import ctypes
import logging
import struct
from dataclasses import dataclass
from pathlib import Path

from .errors import PaleditorError

log = logging.getLogger(__name__)

PAK_MAGIC = 0x5A6F12E1
SUPPORTED_VERSIONS = (11,)
FOOTER_SEARCH = 512
DEFAULT_BLOCK_SIZE = 65536


class PakError(PaleditorError):
    """The archive is not in a form this reader handles."""


@dataclass(frozen=True)
class PakEntry:
    offset: int
    size: int
    uncompressed: int
    method: int


class _Cursor:
    """Little-endian reader with UE's length-prefixed strings."""

    def __init__(self, data: bytes, position: int = 0):
        self.data = data
        self.position = position

    def u32(self) -> int:
        value = struct.unpack_from("<I", self.data, self.position)[0]
        self.position += 4
        return value

    def i32(self) -> int:
        value = struct.unpack_from("<i", self.data, self.position)[0]
        self.position += 4
        return value

    def u64(self) -> int:
        value = struct.unpack_from("<Q", self.data, self.position)[0]
        self.position += 8
        return value

    def take(self, count: int) -> bytes:
        value = self.data[self.position : self.position + count]
        self.position += count
        return value

    def string(self) -> str:
        length = self.i32()
        if length == 0:
            return ""
        if length < 0:
            text = self.take(-length * 2).decode("utf-16-le", "replace")
        else:
            text = self.take(length).decode("utf-8", "replace")
        return text.rstrip("\x00")


class PakFile:
    """Reads one file at a time out of a .pak."""

    def __init__(self, path: Path, oodle_library: str | None = None):
        self.path = Path(path)
        if not self.path.is_file():
            raise PakError(f"{self.path} does not exist")
        self._oodle_library = oodle_library
        self._oodle = None
        self._index: dict[str, int] | None = None
        self._encoded: bytes = b""
        self._read_footer()

    # -- footer and index --------------------------------------------------

    def _read(self, offset: int, length: int) -> bytes:
        with self.path.open("rb") as handle:
            handle.seek(offset)
            return handle.read(length)

    def _read_footer(self) -> None:
        size = self.path.stat().st_size
        tail = self._read(max(0, size - FOOTER_SEARCH), FOOTER_SEARCH)
        found = [
            offset
            for offset in range(len(tail) - 4)
            if struct.unpack_from("<I", tail, offset)[0] == PAK_MAGIC
        ]
        if not found:
            raise PakError(f"{self.path.name} has no pak footer; not a .pak archive")
        footer = tail[found[-1] :]
        _, version = struct.unpack_from("<II", footer, 0)
        if version not in SUPPORTED_VERSIONS:
            raise PakError(
                f"{self.path.name} is pak version {version}; this reader handles "
                f"{', '.join(str(v) for v in SUPPORTED_VERSIONS)}"
            )
        self.version = version
        self.index_offset, self.index_size = struct.unpack_from("<QQ", footer, 8)
        names = footer[44:]
        self.compression_methods = [""] + [
            chunk.rstrip(b"\x00").decode("ascii", "replace")
            for i in range(0, max(0, len(names) - 31), 32)
            if (chunk := names[i : i + 32]).rstrip(b"\x00")
        ]

    def _load_index(self) -> None:
        if self._index is not None:
            return
        cursor = _Cursor(self._read(self.index_offset, self.index_size))
        cursor.string()                      # mount point
        cursor.u32()                         # entry count
        cursor.u64()                         # path hash seed
        if cursor.u32():                     # path hash index present
            cursor.u64(), cursor.u64()
            cursor.take(20)
        if not cursor.u32():
            raise PakError("this pak has no full directory index to read names from")
        directory_offset, directory_size = cursor.u64(), cursor.u64()
        cursor.take(20)
        self._encoded = cursor.take(cursor.u32())

        listing = _Cursor(self._read(directory_offset, directory_size))
        index: dict[str, int] = {}
        for _ in range(listing.u32()):
            directory = listing.string()
            for _ in range(listing.u32()):
                name = listing.string()
                index[directory + name] = listing.u32()
        self._index = index
        log.debug("pak index: %s files", len(index))

    def names(self) -> list[str]:
        self._load_index()
        return sorted(self._index or {})

    # -- entries -----------------------------------------------------------

    def _entry(self, path: str) -> PakEntry:
        self._load_index()
        assert self._index is not None
        if path not in self._index:
            raise PakError(f"{path} is not in {self.path.name}")
        cursor = _Cursor(self._encoded, self._index[path])
        value = cursor.u32()
        method = (value >> 23) & 0x3F
        if (value >> 22) & 1:
            raise PakError(f"{path} is encrypted; this reader cannot read it")
        if (value & 0x3F) == 0x3F:
            cursor.u32()
        offset = cursor.u32() if value & (1 << 31) else cursor.u64()
        uncompressed = cursor.u32() if value & (1 << 30) else cursor.u64()
        size = uncompressed
        if method != 0:
            size = cursor.u32() if value & (1 << 29) else cursor.u64()
        return PakEntry(offset=offset, size=size, uncompressed=uncompressed, method=method)

    def read_file(self, path: str) -> bytes:
        """Extract one file.

        The entry is repeated on disk ahead of its payload, and that copy
        carries the authoritative block table. Its offsets are relative to the
        start of the entry, so the first block begins exactly where the header
        ends - which is why they are read from there rather than recomputed.
        """
        entry = self._entry(path)
        header = self._read(entry.offset, 64 * 1024)
        method = struct.unpack_from("<I", header, 24)[0]
        block_count = struct.unpack_from("<i", header, 48)[0] if method else 0

        if method == 0:
            # No blocks: hash, then the encryption flag and block size.
            return self._read(entry.offset + 53, entry.uncompressed)

        blocks = [
            struct.unpack_from("<qq", header, 52 + i * 16) for i in range(block_count)
        ]
        after_blocks = 52 + block_count * 16
        block_size = struct.unpack_from("<I", header, after_blocks + 1)[0] or DEFAULT_BLOCK_SIZE

        name = self.compression_methods[method] if method < len(self.compression_methods) else "?"
        if name.lower() != "oodle":
            raise PakError(f"{path} uses {name} compression, which is not handled")

        library = self._load_oodle()
        out = bytearray()
        remaining = entry.uncompressed
        for start, end in blocks:
            chunk = self._read(entry.offset + start, end - start)
            wanted = min(block_size, remaining)
            out += library.decompress(chunk, wanted)
            remaining -= wanted
        if len(out) != entry.uncompressed:
            raise PakError(
                f"{path} decompressed to {len(out)} bytes, expected {entry.uncompressed}"
            )
        return bytes(out)

    def _load_oodle(self):
        if self._oodle is None:
            from .saves import oodle

            self._oodle = oodle.load(self._oodle_library)
        return self._oodle
