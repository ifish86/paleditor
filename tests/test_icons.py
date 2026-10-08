"""Item icons and category classification.

Coverage from the wiki is inherently partial: it keys images on display names,
the save keys items on internal ids, and there is no transformation between
them. Measured against a live 470-item catalogue, 63 resolved. So the category
fallback is what actually makes a chest readable, and it is the part that has
to work for every item.

Nothing here touches the network.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from paleditor import icons
from paleditor.catalog import categorise


# -- classification: this one must cover everything ------------------------


@pytest.mark.parametrize(
    "item_id,expected",
    [
        ("PalSphere", "sphere"),
        ("PalSphere_Giga", "sphere"),
        ("PalUpgradeStone2", "soul"),
        ("RifleBullet", "ammo"),
        ("Arrow", "ammo"),
        ("FragGrenade_Elec", "ammo"),
        ("SphereModule_Sniper", "ammo"),
        ("Blueprint_AssaultRifle_Default2", "blueprint"),
        ("SkillCard_ThrowRock", "skill"),
        ("TreasureBoxKey01", "key"),
        ("Money", "currency"),
        ("DogCoin", "currency"),
        ("FurArmorCold", "gear"),
        ("Meat_Boar", "food"),
        ("WheatSeeds", "seed"),
        ("Lotus_hp_01", "medicine"),
        ("ExpBoost_03", "consumable"),
        ("PalEgg_Dark_05", "egg"),
        # Anything unrecognised still lands somewhere: an uncategorised slot
        # would be the one cell that looks broken in a coloured grid.
        ("Wood_Fine", "material"),
        ("SomethingNobodyHasSeen", "material"),
    ],
)
def test_every_item_gets_a_category(item_id, expected):
    assert categorise(item_id) == expected


def test_a_missing_item_id_is_still_safe():
    assert categorise(None) == "material"
    assert categorise("") == "material"


def test_blueprints_beat_the_weapon_they_name():
    """Rule order matters: a rifle blueprint is a blueprint, not a weapon."""
    assert categorise("Blueprint_AssaultRifle_Default2") == "blueprint"
    assert categorise("AssaultRifle_Default3") == "weapon"


# -- name derivation --------------------------------------------------------


@pytest.mark.parametrize(
    "item_id,expected",
    [
        ("PalSphere", "Pal Sphere"),
        ("Wood_Fine", "Wood Fine"),
        ("AssaultRifleBullet", "Assault Rifle Bullet"),
        ("BOSS_Anubis", "Anubis"),
    ],
)
def test_readable_name(item_id, expected):
    assert icons.readable_name(item_id) == expected


def test_a_curated_name_is_tried_before_the_derived_one():
    titles = icons.candidate_titles("PalUpgradeStone", "Pal Soul (S)")
    assert titles[0] == "File:Pal Soul (S).png"
    assert any("Pal Upgrade Stone" in t for t in titles)


def test_an_uncurated_item_only_has_the_derived_name():
    titles = icons.candidate_titles("SkillCard_ThrowRock", "SkillCard_ThrowRock")
    assert all("Skill Card Throw Rock" in t for t in titles)


# -- what comes back off the wire ------------------------------------------


def test_webp_is_accepted():
    """Fandom's CDN content-negotiates: a .png URL commonly returns WebP.

    Rejecting it on the URL's extension silently resolved every icon and saved
    none of them.
    """
    webp = b"RIFF" + struct.pack("<I", 100) + b"WEBPVP8X" + b"\x00" * 32
    assert icons.image_kind(webp) == "webp"


def test_png_jpeg_and_gif_are_accepted():
    assert icons.image_kind(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16) == "png"
    assert icons.image_kind(b"\xff\xd8\xff\xe0" + b"\x00" * 16) == "jpg"
    assert icons.image_kind(b"GIF89a" + b"\x00" * 16) == "gif"


def test_an_error_page_is_not_saved_as_an_icon():
    assert icons.image_kind(b"<!DOCTYPE html><html>404</html>") is None
    assert icons.image_kind(b"") is None


def test_riff_alone_is_not_enough():
    """RIFF is a container; WAV would start the same way."""
    assert icons.image_kind(b"RIFF" + struct.pack("<I", 100) + b"WAVEfmt ") is None


def test_a_filename_cannot_escape_the_icon_directory():
    assert "/" not in icons._safe("../../etc/passwd")
    assert "/" not in icons._safe("a/b/c")
    assert icons._safe("PalSphere_Giga") == "PalSphere_Giga"


# -- serving ----------------------------------------------------------------


def test_an_item_without_an_icon_gives_404_so_the_ui_falls_back(friend_client):
    response = friend_client.get("/api/items/Wood/icon")
    assert response.status_code == 404


def test_an_unknown_item_gives_404(friend_client):
    response = friend_client.get("/api/items/NoSuchItem/icon")
    assert response.status_code == 404


def test_a_downloaded_icon_is_served_with_its_real_media_type(
    friend_client, config, conn
):
    from paleditor import db

    directory = db.icon_dir(config.database.path)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "Wood.webp").write_bytes(
        b"RIFF" + struct.pack("<I", 100) + b"WEBPVP8X" + b"\x00" * 32
    )
    conn.execute("UPDATE items SET icon = 'Wood.webp' WHERE item_id = 'Wood'")
    conn.commit()

    response = friend_client.get("/api/items/Wood/icon")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"


def test_a_recorded_icon_whose_file_vanished_gives_404(friend_client, conn):
    conn.execute("UPDATE items SET icon = 'gone.webp' WHERE item_id = 'Wood'")
    conn.commit()
    assert friend_client.get("/api/items/Wood/icon").status_code == 404


def test_slots_carry_a_category_even_for_uncatalogued_items(friend_client):
    detail = friend_client.get(
        "/api/chests/6d9eb0f3-3c70-42e6-b8e2-4e5274d02609"
    ).json()
    occupied = [s for s in detail["slots"] if s["item_id"]]
    assert occupied
    assert all(s["category"] for s in occupied), "every filled slot needs a colour"


# -- substring false positives ---------------------------------------------


def test_short_keywords_match_whole_words_only():
    """"body" in "Nobody", "ring" in "Spring", "net" in "Magnet".

    Raw substring matching put all three in the wrong category, and the longer
    an item list grows the more of those turn up.
    """
    assert categorise("SomethingNobodyHasSeen") == "material"
    assert categorise("Spring") == "material"
    assert categorise("Magnet") == "material"


def test_long_keywords_still_match_inside_a_word():
    """Multi-word keywords have to, since ids run them together."""
    assert categorise("PalSphere_Giga") == "sphere"
    assert categorise("PalUpgradeStone3") == "soul"
    assert categorise("TreasureBoxKey02") == "key"


def test_digits_do_not_hide_a_word():
    """Head012 splits to ['head', '012'], so the keyword still matches."""
    from paleditor.catalog import tokens

    assert tokens("Head012") == ["head", "012"]
    assert tokens("TreasureBoxKey01") == ["treasure", "box", "key", "01"]
    assert categorise("Head012") == "gear"


def test_the_whole_live_catalogue_classifies(tmp_path):
    """Every id seen in the real world lands in a category."""
    sample = [
        "Wood_Fine", "Pal_crystal_S", "CopperOre", "ManganeseOre", "CrudeOil",
        "YakushimaIngot001", "RainbowCrystal", "MeteorDrop", "Eemerald",
        "Blueprint_SFArmorCold_3", "SkillCard_DiamondFall", "FishingBait_3_A",
        "PalItem_ToSell_05", "Lotus_workspeed_01", "AffectionFruit_02",
        "GrapplingGun3", "PumpActionShotgun", "Bow_triple", "bone",
    ]
    for item_id in sample:
        assert categorise(item_id), item_id


# -- readable names ---------------------------------------------------------


@pytest.mark.parametrize(
    "item_id,expected",
    [
        # The kind goes last, the way the game words it.
        ("SkillCard_ThrowRock", "Throw Rock Skill Card"),
        ("Blueprint_LaserRifle_2", "Laser Rifle Blueprint 2"),
        ("SphereModule_Sniper", "Sniper Sphere Module"),
        ("Meat_BerryGoat", "Berry Goat Meat"),
        # A trailing tier belongs after the kind, not in front of it.
        ("ExpBoost_03", "EXP Boost 3"),
        ("PalEgg_Dark_05", "Dark Pal Egg 5"),
        # Plain camelCase and lower-case ids.
        ("CopperOre", "Copper Ore"),
        ("PumpActionShotgun", "Pump Action Shotgun"),
        ("bone", "Bone"),
        # Acronyms the split cannot recover alone.
        ("Accessory_HP_1", "HP Accessory 1"),
        ("Blueprint_SFArmorCold_3", "SF Armor Cold Blueprint 3"),
    ],
)
def test_a_readable_name_is_derived_from_the_id(item_id, expected):
    """Not the exact in-game string - that lives in the game's localisation
    data, which a dedicated server build does not ship - but much closer than
    the raw id, and it covers every item."""
    from paleditor.catalog import derive_display_name

    assert derive_display_name(item_id) == expected


def test_an_id_that_derives_to_nothing_falls_back_to_itself():
    from paleditor.catalog import derive_display_name

    assert derive_display_name("_") == "_"


def test_observed_items_are_recorded_with_a_derived_name(config, conn):
    from paleditor import ingest

    ingest.run(config, conn=conn)
    row = conn.execute(
        "SELECT display_name FROM items WHERE item_id = 'Paldium'"
    ).fetchone()
    assert row["display_name"] == "Paldium"

    conn.execute(
        "INSERT INTO items(item_id, display_name, category, provenance) "
        "VALUES ('SkillCard_TestOnly', 'SkillCard_TestOnly', 'skill', 'observed')"
    )
    conn.commit()
    from paleditor import catalog

    catalog.record_observed(conn)
    row = conn.execute(
        "SELECT display_name FROM items WHERE item_id = 'SkillCard_TestOnly'"
    ).fetchone()
    assert row["display_name"] == "Test Only Skill Card"


def test_a_curated_name_is_never_overwritten_by_a_derived_one(config, conn):
    """items.json is the authority: 'Quality Wood' must not become 'Wood Fine'."""
    from paleditor import catalog, ingest

    ingest.run(config, conn=conn)
    catalog.record_observed(conn)
    row = conn.execute(
        "SELECT display_name, provenance FROM items WHERE item_id = 'Wood_Fine'"
    ).fetchone()
    assert row["provenance"] == "seed"
    assert row["display_name"] == "Quality Wood"
