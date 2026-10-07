"""Oodle (Mermaid/Kraken) decompression, via a libooz shared object.

This server's saves use the PlM container, which is Oodle-compressed. There is
no pure-Python decoder for it, so paleditor loads a shared library through
ctypes and calls `ooz_decompress`.

The library is not shipped with paleditor and is not vendored here. Its
upstream, powzix/ooz, declares no licence, which under default copyright means
it is not ours to redistribute in any form. `scripts/oodle/build.sh` fetches a
pinned commit, builds a decompressor and verifies it against a real save before
installing; see docs/oodle.md.

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

def _repo_lib() -> Path:
    """lib/libooz.so beside the source tree, where scripts/oodle/build.sh puts it."""
    return Path(__file__).resolve().parents[3] / "lib" / "libooz.so"


# Checked in order. The first that loads and exposes ooz_decompress wins.
# These are all paleditor's own locations: nothing here depends on another
# project being installed or on where somebody happened to put one.
SEARCH_PATHS: tuple[Path, ...] = (
    Path("/opt/paleditor/lib/libooz.so"),
    Path.home() / ".local" / "lib" / "libooz.so",
    Path("/usr/local/lib/libooz.so"),
    Path("/usr/lib/libooz.so"),
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
    candidates = (
        [Path(explicit)] if explicit else [_repo_lib(), *SEARCH_PATHS]
    )
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
        "no usable Oodle library found, so this server's PlM (Oodle) saves "
        "cannot be read.\n\n"
        "Build one:\n"
        "    scripts/oodle/build.sh\n\n"
        "It fetches a pinned upstream commit, compiles a decompressor, checks "
        "it against\nyour save, and installs it. paleditor cannot ship the "
        "library itself: its upstream\ndeclares no licence, so it is not ours "
        "to redistribute. See docs/oodle.md.\n\n"
        "Or point [palworld] oodle_library at a build you already have.\n\n"
        "Tried:\n  " + "\n  ".join(errors)
    )


def available(explicit: str | os.PathLike[str] | None = None) -> bool:
    try:
        load(explicit)
    except ParserUnavailable:
        return False
    return True
