"""The save layer: container headers, RawData blob codecs, classification.

The byte layouts asserted here were read back from a live world on 2026-10-07.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import replace
from pathlib import Path

import pytest

from paleditor.errors import SaveFormatError
from paleditor.saves import blobs, container
from paleditor.saves import fieldpaths as fp


def make_save(payload: bytes, *, magic: bytes = container.MAGIC_ZLIB) -> bytes:
    compressed = zlib.compress(payload)
    return (
        struct.pack("<II", len(payload), len(compressed))
        + magic
        + bytes([container.TYPE_SINGLE])
        + compressed
    )


# Large enough that lopping bytes off leaves a plausible-looking file
# rather than one too short to hold a header.
GVAS = b"GVAS" + bytes(range(256)) * 40


# -- container -------------------------------------------------------------


def test_reads_a_zlib_save():
    box = container.read(make_save(GVAS))
    assert box.gvas == GVAS
    assert box.was_oodle is False


def test_round_trips_through_write():
    box = container.read(container.write(GVAS))
    assert box.gvas == GVAS


def test_refuses_a_file_that_is_not_a_save():
    with pytest.raises(SaveFormatError, match="magic"):
        container.read(b"\x00" * 64)


def test_refuses_a_truncated_save():
    """The torn-read guard.

    The server rewrites Level.sav every ~30s. A read landing mid-write gives a
    file whose declared compressed length exceeds what is actually present.
    """
    raw = make_save(GVAS)
    with pytest.raises(SaveFormatError, match="truncated"):
        container.read(raw[: len(raw) - 20])


def test_refuses_a_payload_that_is_not_gvas():
    with pytest.raises(SaveFormatError, match="GVAS"):
        container.read(make_save(b"NOPE" + b"\x00" * 64))


def test_refuses_to_compress_something_that_is_not_gvas():
    with pytest.raises(SaveFormatError, match="not GVAS"):
        container.write(b"nope")


def test_write_always_produces_the_zlib_container():
    """A PlM world is written back as PlZ, because no Oodle compressor works."""
    raw = container.write(GVAS)
    assert raw[8:11] == container.MAGIC_ZLIB


def test_an_empty_header_is_refused():
    with pytest.raises(SaveFormatError):
        container.read(b"")


# -- slot blobs ------------------------------------------------------------

SLOT = (
    struct.pack("<iii", 2, 11, 6) + b"Arrow\x00" + b"\x00" * 52
)


def test_decodes_a_slot():
    slot = blobs.decode_slot(SLOT)
    assert (slot.slot_index, slot.stack_count, slot.item_id) == (2, 11, "Arrow")
    assert len(slot.tail) == 52


def test_a_slot_re_encodes_byte_for_byte():
    assert blobs.encode_slot(blobs.decode_slot(SLOT)) == SLOT


def test_changing_an_item_changes_the_blob_length():
    """Item ids are length-prefixed, so a longer id makes a longer blob.

    The enclosing array size is rewritten by the GVAS writer, which is why
    paleditor edits the structure rather than patching bytes in place.
    """
    changed = replace(blobs.decode_slot(SLOT), item_id="TreasureBoxKey01")
    out = blobs.encode_slot(changed)
    assert len(out) > len(SLOT)
    assert blobs.decode_slot(out).item_id == "TreasureBoxKey01"
    assert blobs.decode_slot(out).tail == blobs.decode_slot(SLOT).tail


def test_a_truncated_slot_blob_is_refused():
    with pytest.raises(SaveFormatError):
        blobs.decode_slot(b"\x00\x00")


def test_a_slot_whose_string_runs_past_the_end_is_refused():
    with pytest.raises(SaveFormatError, match="past the end"):
        blobs.decode_slot(struct.pack("<iii", 0, 1, 9999) + b"short")


# -- password locks --------------------------------------------------------

LOCK_SET = b"\x01\x05\x00\x00\x007826\x00\x01\x00\x00\x00\xa1\xe2\x98?\x00\x00"
LOCK_UNSET = b"\x01\x00\x00\x00\x00" + b"\x00" * 8


def test_decodes_a_lock_code():
    lock = blobs.decode_password_lock(LOCK_SET)
    assert lock.code == "7826"
    assert lock.is_set


def test_a_lock_with_no_code_is_not_the_same_as_no_lock():
    """An object can be lockable with no code set.

    Treating that as "unlocked" would be a lie, and treating it as "no lock"
    would hide a chest the owner can still put a code on.
    """
    lock = blobs.decode_password_lock(LOCK_UNSET)
    assert lock.code == ""
    assert lock.is_set is False


@pytest.mark.parametrize("blob", [LOCK_SET, LOCK_UNSET])
def test_a_lock_re_encodes_byte_for_byte(blob):
    assert blobs.encode_password_lock(blobs.decode_password_lock(blob)) == blob


def test_setting_a_code_on_an_unlocked_object():
    lock = replace(blobs.decode_password_lock(LOCK_UNSET), code="4321")
    assert blobs.decode_password_lock(blobs.encode_password_lock(lock)).code == "4321"


# -- container ids ---------------------------------------------------------


def test_a_zero_container_id_reads_as_absent():
    assert blobs.container_id_bytes(b"\x00" * 16) is None


def test_a_real_container_id_comes_back():
    raw = bytes(range(16))
    assert blobs.container_id_bytes(raw + b"trailing") == raw


def test_a_short_blob_has_no_container_id():
    assert blobs.container_id_bytes(b"\x01\x02") is None


# -- transforms ------------------------------------------------------------


def test_decodes_a_transform():
    blob = b"\x00" * 32 + struct.pack("<3d", -11084.9, 292666.8, 14984.5) + struct.pack("<3d", 1.0, 1.0, 1.0)
    assert blobs.decode_transform(blob) == pytest.approx((-11084.9, 292666.8, 14984.5))


def test_takes_the_first_scale_vector_not_the_last():
    """Base camp blobs carry a second (1,1,1) preceded by a bounding box.

    Reading backwards returns that box for every base and puts them all in the
    same place.
    """
    real = struct.pack("<3d", -358126.5, 269555.3, 7872.7)
    box = struct.pack("<3d", -170.0, 0.0, 170.0)
    one = struct.pack("<3d", 1.0, 1.0, 1.0)
    blob = b"\x00" * 16 + real + one + b"\x00" * 40 + box + one
    assert blobs.decode_transform(blob) == pytest.approx((-358126.5, 269555.3, 7872.7))


def test_no_transform_in_an_empty_blob():
    assert blobs.decode_transform(b"") is None
    assert blobs.decode_transform(b"\x00" * 200) is None


# -- object classification -------------------------------------------------


@pytest.mark.parametrize(
    "object_type,expected",
    [
        ("ItemChest", "storage"),
        ("ItemChest_02", "storage"),
        ("ItemChest_03", "storage"),
        ("Container01_Iron", "storage"),
        ("Shelf04_Iron", "storage"),
        # 1,443 of these in the reference world against ~65 real chests.
        ("TreasureBox", "loot"),
        ("TreasureBox_Oilrig", "loot"),
        ("TreasureBox_FishingJunk_RequiredLongHold2", "loot"),
        ("CommonDropItem3D", "loot"),
        ("PalFoodBox", "station"),
        ("PalMedicineBox", "station"),
        ("BlastFurnace3", "station"),
        ("PalBox", "station"),
        # Doors own no container; they are kept only when they carry a code.
        ("Stone_DoorWall", None),
        ("Wooden_Foundation", None),
        (None, None),
    ],
)
def test_classification(object_type, expected):
    assert fp.classify(object_type) == expected


def test_the_status_report_no_longer_claims_anything_is_guessed():
    report = fp.status_report()
    assert report["lock code"].startswith("CONFIRMED")
    assert report["container GUID"].startswith("CONFIRMED")
    # Confirmed on a live server, but still gated per deployment: a future
    # game version could stop accepting it, and a world that will not load is
    # an expensive way to find out.
    container_swap = report["writing PlZ over a PlM world"]
    assert container_swap.startswith("CONFIRMED")
    assert "plz_write_confirmed" in container_swap
