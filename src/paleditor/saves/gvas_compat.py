"""Scalar property types palworld-save-tools does not handle inside maps.

A map's keys and values go through `FArchiveReader.prop_value`, which knows
only StructProperty, EnumProperty, NameProperty, IntProperty and BoolProperty.
Anything else raises:

    Unknown property value type: Int64Property
    (.worldSaveData.LevelObjectRecoverPartySaveData.Value.PlayerLastUsedTimes.Value)

That is a map of player GUID to a 64-bit timestamp, and it is enough to stop
the entire world parsing. It appeared in this server's save between September
and October 2026; an older copy of the same world parses without it. More of
these will show up, because the parser's last release was October 2024.

Rather than fork the library, the missing scalar types are added to both the
reader and the writer here. Both, symmetrically: the write path depends on
`GvasFile.write` reproducing the input byte for byte, so a type the reader
understands and the writer does not would corrupt a save rather than fail.

Only unambiguous fixed-width scalars and strings are added. A type whose wire
format is not obvious is left to raise, because a wrong guess here is written
back into the world.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# property type name -> the reader/writer primitive that handles it. The two
# classes expose the same method names, which is what makes this symmetric.
SCALAR_TYPES: dict[str, str] = {
    "Int64Property": "i64",
    "UInt64Property": "u64",
    "UInt32Property": "u32",
    "Int16Property": "i16",
    "UInt16Property": "u16",
    "FloatProperty": "float",
    "DoubleProperty": "double",
    "StrProperty": "fstring",
}

_patched = False


def apply() -> None:
    """Extend prop_value on both archive classes. Safe to call repeatedly."""
    global _patched
    if _patched:
        return

    from palworld_save_tools.archive import FArchiveReader, FArchiveWriter

    original_read = FArchiveReader.prop_value
    original_write = FArchiveWriter.prop_value

    def read_prop_value(self, type_name: str, struct_type_name: str, path: str):
        primitive = SCALAR_TYPES.get(type_name)
        if primitive is not None:
            return getattr(self, primitive)()
        return original_read(self, type_name, struct_type_name, path)

    def write_prop_value(self, type_name: str, struct_type_name: str, value):
        primitive = SCALAR_TYPES.get(type_name)
        if primitive is not None:
            return getattr(self, primitive)(value)
        return original_write(self, type_name, struct_type_name, value)

    FArchiveReader.prop_value = read_prop_value
    FArchiveWriter.prop_value = write_prop_value
    _patched = True
    log.debug(
        "extended map property handling with: %s", ", ".join(sorted(SCALAR_TYPES))
    )
