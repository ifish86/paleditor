"""Scalar property types the parser does not handle inside maps.

A map's keys and values go through prop_value, which upstream limits to five
types. This world grew a map of player GUID to a 64-bit timestamp
(LevelObjectRecoverPartySaveData.PlayerLastUsedTimes) between September and
October 2026, and that one unhandled type stopped the entire world parsing.
"""

from __future__ import annotations

import struct

import pytest

from paleditor.saves import gvas_compat

pytest.importorskip("palworld_save_tools")


@pytest.fixture(autouse=True)
def patched():
    gvas_compat.apply()


def reader(raw: bytes):
    from palworld_save_tools.archive import FArchiveReader

    return FArchiveReader(raw)


def writer():
    from palworld_save_tools.archive import FArchiveWriter

    return FArchiveWriter()


@pytest.mark.parametrize(
    "type_name,fmt,value",
    [
        ("Int64Property", "<q", 1759862051123),
        ("Int64Property", "<q", -1),
        ("UInt64Property", "<Q", 2**64 - 1),
        ("UInt32Property", "<I", 2**32 - 1),
        ("Int16Property", "<h", -32768),
        ("UInt16Property", "<H", 65535),
        ("DoubleProperty", "<d", 3.141592653589793),
        ("FloatProperty", "<f", 2.5),
    ],
)
def test_a_scalar_map_value_reads_and_writes_back_identically(type_name, fmt, value):
    """Symmetry matters more than reading.

    The write path depends on GvasFile.write reproducing its input byte for
    byte, so a type the reader understands and the writer does not would
    corrupt a world rather than fail to parse one.
    """
    raw = struct.pack(fmt, value)
    got = reader(raw).prop_value(type_name, "", ".test")
    assert got == pytest.approx(value) if isinstance(value, float) else got == value

    out = writer()
    out.prop_value(type_name, "", got)
    assert out.bytes() == raw


def test_the_type_that_actually_broke_this_world():
    """PlayerLastUsedTimes: a GUID-keyed map of 64-bit timestamps."""
    timestamp = 638_000_000_000_000_000
    raw = struct.pack("<q", timestamp)
    assert reader(raw).prop_value("Int64Property", "", ".t") == timestamp


def test_the_types_upstream_already_handles_still_work():
    value = reader(struct.pack("<i", 1234)).prop_value("IntProperty", "", ".t")
    assert value == 1234


def test_an_unknown_type_still_raises_rather_than_guessing():
    """A type whose layout is not obvious must not be invented.

    The same table writes the save back, so a wrong guess is written into the
    world rather than merely misread.
    """
    with pytest.raises(Exception, match="Unknown property value type"):
        reader(b"\x00" * 32).prop_value("SomeFutureProperty", "", ".t")


def test_applying_twice_does_not_stack_wrappers():
    from palworld_save_tools.archive import FArchiveReader

    first = FArchiveReader.prop_value
    gvas_compat.apply()
    gvas_compat.apply()
    assert FArchiveReader.prop_value is first


def test_every_mapped_primitive_exists_on_both_classes():
    """The table is only safe because reader and writer share method names."""
    from palworld_save_tools.archive import FArchiveReader, FArchiveWriter

    for type_name, primitive in gvas_compat.SCALAR_TYPES.items():
        assert hasattr(FArchiveReader, primitive), f"{type_name} -> reader.{primitive}"
        assert hasattr(FArchiveWriter, primitive), f"{type_name} -> writer.{primitive}"


def test_the_failure_points_at_the_one_line_fix():
    from paleditor.saves.palworld import _drift_hint

    hint = _drift_hint(
        Exception(
            "Unknown property value type: Int64Property "
            "(.worldSaveData.LevelObjectRecoverPartySaveData.Value.PlayerLastUsedTimes.Value)"
        )
    )
    assert "gvas_compat.py" in hint
    assert "SCALAR_TYPES" in hint
    # And warns against the dangerous half of the advice.
    assert "Do not" in hint and "guess" in hint


def test_an_unrelated_parser_error_gets_no_spurious_hint():
    from paleditor.saves.palworld import _drift_hint

    assert _drift_hint(Exception("EOF not reached")) == ""
