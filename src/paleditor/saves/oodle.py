"""Oodle (Mermaid/Kraken) decompression, via a libooz shared object.

This server's saves use the PlM container, which is Oodle-compressed. There is
no pure-Python decoder for it, so paleditor loads a shared library through
ctypes. The ooz project (an open reimplementation) provides `ooz_decompress`;
palstats ships a prebuilt `libooz.so` that works.

The library is NOT vendored in this repo: it is a third-party binary, and which
build works depends on the host. Point [palworld] oodle_library at it, or drop
it somewhere on the search path below.

Compression is deliberately not exposed. `OodLZ_Compress` is present in the
library but segfaults when called, so paleditor writes the zlib (PlZ) container
instead; see container.py.
"""

from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path

from ..errors import ParserUnavailable

log = logging.getLogger(__name__)

# Checked in order. The first that loads and exposes ooz_decompress wins.
SEARCH_PATHS: tuple[Path, ...] = (
    Path("/opt/paleditor/lib/libooz.so"),
    Path("/usr/local/lib/libooz.so"),
    Path("/usr/lib/libooz.so"),
    # palstats ships a working build; honour it when the two sit side by side.
    Path(__file__).resolve().parents[3] / "palstats" / "libooz.so",
)

_cache: dict[str, "OodleLibrary"] = {}


class OodleLibrary:
    def __init__(self, path: Path):
        self.path = path
        try:
            self._lib = ctypes.CDLL(str(path))
        except OSError as exc:
            raise ParserUnavailable(f"cannot load {path}: {exc}") from exc
        try:
            self._decompress = self._lib.ooz_decompress
        except AttributeError as exc:
            raise ParserUnavailable(
                f"{path} does not export ooz_decompress; it is not an ooz build"
            ) from exc
        self._decompress.argtypes = [
            ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t,
        ]
        self._decompress.restype = ctypes.c_int

    def decompress(self, payload: bytes, uncompressed_size: int) -> bytes:
        """Decompress ``payload`` to exactly ``uncompressed_size`` bytes.

        The caller must already know the uncompressed size; the save header
        carries it. A short result means a corrupt or truncated payload, which
        is raised rather than returned, because a partial world must never
        reach ingest.
        """
        if uncompressed_size <= 0:
            raise ValueError("uncompressed_size must be positive")
        # The decoder can overrun slightly on malformed input; the pad is what
        # the ooz reference implementation recommends.
        buffer = ctypes.create_string_buffer(uncompressed_size + 64)
        written = self._decompress(
            payload, len(payload), buffer, uncompressed_size
        )
        if written != uncompressed_size:
            raise ParserUnavailable(
                f"oodle decompression produced {written} bytes, expected "
                f"{uncompressed_size}; the save is corrupt or was read mid-write"
            )
        return buffer.raw[:written]


def load(explicit: str | os.PathLike[str] | None = None) -> OodleLibrary:
    """Find and load libooz, caching the result."""
    candidates = [Path(explicit)] if explicit else list(SEARCH_PATHS)
    errors = []
    for candidate in candidates:
        key = str(candidate)
        if key in _cache:
            return _cache[key]
        if not candidate.is_file():
            errors.append(f"{candidate}: not found")
            continue
        try:
            library = OodleLibrary(candidate)
        except ParserUnavailable as exc:
            errors.append(str(exc))
            continue
        log.info("loaded oodle library from %s", candidate)
        _cache[key] = library
        return library
    raise ParserUnavailable(
        "no usable Oodle library found, so PlM (Oodle) saves cannot be read.\n"
        "Set [palworld] oodle_library to a libooz.so build, or place one at "
        "/opt/paleditor/lib/libooz.so.\nTried:\n  " + "\n  ".join(errors)
    )


def available(explicit: str | os.PathLike[str] | None = None) -> bool:
    try:
        load(explicit)
    except ParserUnavailable:
        return False
    return True
