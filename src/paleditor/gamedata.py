"""Item names read out of the game's own data.

The save stores internal ids - ``Wood_Fine``, ``PalUpgradeStone2`` - and the
game displays something else entirely. Guessing at the difference does not
work: ``Wood_Fine`` is Hardwood, not "Quality Wood"; ``CopperOre`` is just Ore;
``Pal_crystal_S`` is a Paldium Fragment. Hand-curated names in data/items.json
got several of these wrong.

The mapping is in the game's own localisation table, which the dedicated server
ships even though it never renders any of it:

    Pal/Content/L10N/en/Pal/DataTable/Text/DT_ItemNameText_Common.uexp

Rows are keyed ``ITEM_NAME_<ItemId>_TextData`` and followed by the display
string. Reading it gives both every item id the game knows and what it calls
them, which is what the item picker needs: it used to offer only the few
hundred ids that happened to exist somewhere in the world already.

Nothing here is committed. The table is read from the operator's own install.
"""

from __future__ import annotations

import logging
import re
import struct
from pathlib import Path

from .errors import PaleditorError
from .pak import PakError, PakFile

log = logging.getLogger(__name__)

NAME_TABLE = "Pal/Content/L10N/{language}/Pal/DataTable/Text/DT_ItemNameText_Common.uexp"
ROW_KEY = re.compile(r"^ITEM_NAME_(.+)_TextData$")

# Where the paks sit relative to a save directory:
#   <root>/Pal/Saved/SaveGames/0/<world-id>   ->  <root>/Pal/Content/Paks
SAVE_DIR_DEPTH = 4
PAK_SUBPATH = ("Content", "Paks")
PAK_NAMES = ("Pal-LinuxServer.pak", "Pal-WindowsServer.pak", "Pal-Windows.pak")

MIN_STRING = 2
MAX_STRING = 300


def find_pak(save_dir: Path) -> Path | None:
    """The server's pak, inferred from where the save lives."""
    root = Path(save_dir)
    for _ in range(SAVE_DIR_DEPTH):
        root = root.parent
    paks = root.joinpath(*PAK_SUBPATH)
    if not paks.is_dir():
        return None
    for name in PAK_NAMES:
        candidate = paks / name
        if candidate.is_file():
            return candidate
    found = sorted(paks.glob("*.pak"))
    return found[0] if found else None


def _strings(buffer: bytes) -> list[str]:
    """Every length-prefixed string in a serialised asset, in order.

    The table is walked rather than parsed: a DataTable's binary layout moves
    between engine versions, but a counted string does not, and the rows are
    stored key-then-value in order.
    """
    out: list[str] = []
    position = 0
    end = len(buffer) - 4
    while position < end:
        length = struct.unpack_from("<i", buffer, position)[0]
        if MIN_STRING <= length <= MAX_STRING:
            raw = buffer[position + 4 : position + 4 + length]
            if (
                len(raw) == length
                and raw.endswith(b"\x00")
                and all(32 <= byte < 127 for byte in raw[:-1])
            ):
                out.append(raw[:-1].decode("ascii"))
                position += 4 + length
                continue
        elif -MAX_STRING <= length <= -MIN_STRING:
            size = -length * 2
            raw = buffer[position + 4 : position + 4 + size]
            if len(raw) == size and raw.endswith(b"\x00\x00"):
                try:
                    out.append(raw[:-2].decode("utf-16-le"))
                    position += 4 + size
                    continue
                except UnicodeDecodeError:
                    pass
        position += 1
    return out


def item_names(pak: Path, *, language: str = "en", oodle_library=None) -> dict[str, str]:
    """Read ``item_id -> display name`` from a game install."""
    archive = PakFile(pak, oodle_library=oodle_library)
    path = NAME_TABLE.format(language=language)
    try:
        payload = archive.read_file(path)
    except PakError as exc:
        raise PaleditorError(
            f"could not read the item name table from {pak.name}: {exc}"
        ) from exc

    found = _strings(payload)
    names: dict[str, str] = {}
    for index, text in enumerate(found):
        match = ROW_KEY.match(text)
        if match and index + 1 < len(found):
            display = found[index + 1].strip()
            if display:
                names[match.group(1)] = display
    if not names:
        raise PaleditorError(
            f"{path} parsed but contained no ITEM_NAME rows; the table layout "
            "has probably changed"
        )
    log.info("read %s item names from %s", len(names), pak.name)
    return names
