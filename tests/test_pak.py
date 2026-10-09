"""The .pak reader.

Only what paleditor needs: find one file, decode its entry, get its bytes.
Builds a small archive rather than relying on a 4GB game install.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from paleditor.pak import PakError, PakFile


def counted(text: str) -> bytes:
    raw = text.encode("utf-8") + b"\x00"
    return struct.pack("<i", len(raw)) + raw


def build_pak(path: Path, files: dict[str, bytes]) -> Path:
    """A version 11 pak with uncompressed entries."""
    body = bytearray()
    placed: dict[str, int] = {}
    for name, payload in files.items():
        placed[name] = len(body)
        body += struct.pack("<qqq", 0, len(payload), len(payload))  # offset/size/unc
        body += struct.pack("<I", 0)                                # method: none
        body += b"\x00" * 20                                        # hash
        body += b"\x00"                                             # bEncrypted
        body += struct.pack("<I", 0)                                # block size
        body += payload

    encoded = bytearray()
    encoded_at: dict[str, int] = {}
    for name, offset in placed.items():
        encoded_at[name] = len(encoded)
        flags = (1 << 31) | (1 << 30)        # 32-bit offset and size
        encoded += struct.pack("<I", flags)
        encoded += struct.pack("<I", offset)
        encoded += struct.pack("<I", len(files[name]))

    directory = bytearray()
    directory += struct.pack("<I", 1)        # one directory
    directory += counted("Root/")
    directory += struct.pack("<I", len(files))
    for name in files:
        directory += counted(name)
        directory += struct.pack("<I", encoded_at[name])

    index = bytearray()
    index += counted("../../../")
    index += struct.pack("<I", len(files))
    index += struct.pack("<Q", 0)            # path hash seed
    index += struct.pack("<I", 0)            # no path hash index
    index += struct.pack("<I", 1)            # full directory index follows
    directory_offset = len(body)
    index_offset = directory_offset + len(directory)
    index += struct.pack("<QQ", directory_offset, len(directory))
    index += b"\x00" * 20
    index += struct.pack("<I", len(encoded))
    index += encoded

    footer = bytearray()
    footer += struct.pack("<II", 0x5A6F12E1, 11)
    footer += struct.pack("<QQ", index_offset, len(index))
    footer += b"\x00" * 20
    footer += b"Oodle".ljust(32, b"\x00") + b"\x00" * 96

    path.write_bytes(bytes(body + directory + index + footer))
    return path


def test_reads_a_file_back(tmp_path):
    pak = build_pak(tmp_path / "test.pak", {"hello.txt": b"the contents"})
    assert PakFile(pak).read_file("Root/hello.txt") == b"the contents"


def test_lists_its_files(tmp_path):
    pak = build_pak(tmp_path / "test.pak", {"a.txt": b"a", "b.txt": b"bb"})
    assert PakFile(pak).names() == ["Root/a.txt", "Root/b.txt"]


def test_a_missing_file_is_named_in_the_error(tmp_path):
    pak = build_pak(tmp_path / "test.pak", {"a.txt": b"a"})
    with pytest.raises(PakError, match="Root/nope.txt"):
        PakFile(pak).read_file("Root/nope.txt")


def test_something_that_is_not_a_pak_is_refused(tmp_path):
    path = tmp_path / "not.pak"
    path.write_bytes(b"just some bytes" * 100)
    with pytest.raises(PakError, match="no pak footer"):
        PakFile(path)


def test_a_missing_archive_is_refused(tmp_path):
    with pytest.raises(PakError, match="does not exist"):
        PakFile(tmp_path / "absent.pak")


def test_an_unsupported_version_says_so(tmp_path):
    pak = build_pak(tmp_path / "test.pak", {"a.txt": b"a"})
    raw = bytearray(pak.read_bytes())
    # Rewrite the version in the footer.
    position = raw.rindex(struct.pack("<I", 0x5A6F12E1))
    struct.pack_into("<I", raw, position + 4, 99)
    pak.write_bytes(bytes(raw))
    with pytest.raises(PakError, match="version 99"):
        PakFile(pak)


def test_the_compression_methods_are_read(tmp_path):
    pak = build_pak(tmp_path / "test.pak", {"a.txt": b"a"})
    assert "Oodle" in PakFile(pak).compression_methods
