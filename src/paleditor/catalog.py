"""The item catalogue: seeding the curated rows, naming the observed ones.

Separate from both the app and ingest because both need it, and the order
matters: seeding must happen before ingest records unknown ids, or a seeded
item gets filed as 'observed' with its raw id for a display name.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from importlib import resources

log = logging.getLogger(__name__)

# Keyword rules for sorting an item id into a category, checked in order. This
# is how the UI gives every slot a readable colour: the world holds hundreds of
# ids nobody has named, and "all ammo" or "all ore" is most of what someone
# wants to see when they open a chest.
#
# Matching is on the raw id rather than the display name, because the display
# name is usually just the id again for anything uncurated.
CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sphere",     ("palsphere",)),
    ("soul",       ("upgradestone", "soul")),
    ("egg",        ("palegg", "hatching")),
    ("blueprint",  ("blueprint", "schematic")),
    ("skill",      ("skillcard",)),
    ("key",        ("treasureboxkey", "dungeonkey")),
    ("ammo",       ("bullet", "arrow", "shell", "rocket", "grenade", "ammo",
                    "gunpowder", "spheremodule")),
    ("weapon",     ("rifle", "pistol", "shotgun", "bow", "crossbow", "launcher",
                    "sword", "axe", "spear", "katana", "club", "hammer", "gun",
                    "blade", "knuckle", "gatling", "flamethrower", "handgun")),
    ("gear",       ("armor", "helmet", "shield", "glider", "accessory", "head",
                    "body", "hat", "mask", "boots", "gloves", "belt", "ring",
                    "amulet", "pendant", "necklace", "grapplin")),
    ("medicine",   ("medicine", "bandage", "antidote", "nutrient", "suppressant",
                    "ointment", "lotus")),
    ("consumable", ("expboost", "affectionfruit", "genderreverse", "potion")),
    ("seed",       ("seeds",)),
    ("food",       ("berries", "wheat", "lettuce", "tomato", "carrot", "milk",
                    "egg", "meat", "bread", "salad", "soup", "pizza", "cake",
                    "juice", "jam", "honey", "mushroom", "fish", "stew",
                    "sandwich", "minestrone", "baked", "grilled", "cooked",
                    "pancake", "curry", "skewer", "omelet", "donut", "sushi",
                    "flour", "herbs", "poppy", "sweet")),
    ("currency",   ("money", "coin", "ticket", "medal")),
    ("tool",       ("pickaxe", "torch", "net", "rod", "bait", "key", "map",
                    "glasses", "lamp", "pack", "bag")),
)


# A short keyword has to match a whole word, not a substring. "body" inside
# "Nobody", "ring" inside "Spring" and "net" inside "Magnet" all matched before
# this, and the longer an item list grows the more of those appear. Keywords
# this long or longer are distinctive enough to match anywhere in the id, which
# is what makes multi-word ones like "palsphere" and "upgradestone" work.
WHOLE_WORD_BELOW = 6

_TOKEN_SPLIT = re.compile(
    r"_+|(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])"
)


def tokens(item_id: str) -> list[str]:
    """Split an id into lowercase words: Head012 -> ['head', '012']."""
    return [part.lower() for part in _TOKEN_SPLIT.split(item_id) if part]


# Internal ids put the kind first; the game puts it last. Reordering these
# turns "SkillCard_ThrowRock" into "Throw Rock Skill Card", which is what
# somebody looking at the item in game would recognise.
TRAILING_KINDS: tuple[tuple[str, str], ...] = (
    ("Blueprint_", "Blueprint"),
    ("SkillCard_", "Skill Card"),
    ("SphereModule_", "Sphere Module"),
    ("PalEgg_", "Pal Egg"),
    ("Meat_", "Meat"),
    ("TechnologyBook_", "Technology Book"),
    ("AncientTechnologyBook_", "Ancient Technology Book"),
    ("FishingBait_", "Fishing Bait"),
    ("ExpBoost_", "EXP Boost"),
    ("Accessory_", "Accessory"),
)

# Spellings the split cannot recover on its own.
WORD_FIXES: dict[str, str] = {
    "Pal": "Pal",
    "Hp": "HP",
    "Sf": "SF",
    "Ar": "AR",
    "Npc": "NPC",
    "Exp": "EXP",
    "Sp": "SP",
}

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[A-Za-z])(?=[0-9])")


def derive_display_name(item_id: str) -> str:
    """A readable name for an id nobody has curated.

    Not the exact in-game string - that lives in the game's localisation data,
    which a dedicated server build does not ship - but much closer than the raw
    id, and it covers every item rather than the handful the wiki can match.
    """
    text = item_id
    suffix = ""
    for prefix, kind in TRAILING_KINDS:
        if text.startswith(prefix):
            text = text[len(prefix):]
            suffix = f" {kind}"
            break

    words: list[str] = []
    for chunk in text.split("_"):
        if not chunk:
            continue
        for word in _CAMEL.split(chunk):
            if not word:
                continue
            if word.isdigit():
                words.append(word.lstrip("0") or "0")
            else:
                capitalised = word.capitalize()
                words.append(WORD_FIXES.get(capitalised, capitalised))

    # Trailing tier markers belong after the kind, not in front of it:
    # ExpBoost_03 reads as "EXP Boost 3", never "3 EXP Boost".
    tier: list[str] = []
    while words and (words[-1].isdigit() or (len(words[-1]) == 1 and words[-1].isalpha())):
        tier.insert(0, words.pop())

    parts = [" ".join(words).strip(), suffix.strip(), " ".join(tier).strip()]
    name = " ".join(part for part in parts if part)
    return name or item_id


def categorise(item_id: str | None) -> str:
    """Sort an item id into a category, defaulting to 'material'.

    Everything lands somewhere on purpose: an uncategorised slot would be the
    one that looks broken in a grid where every other cell is coloured.
    """
    if not item_id:
        return "material"
    lowered = item_id.lower()
    words = set(tokens(item_id))
    for category, keywords in CATEGORY_RULES:
        for keyword in keywords:
            if len(keyword) >= WHOLE_WORD_BELOW:
                if keyword in lowered:
                    return category
            elif keyword in words:
                return category
    return "material"


def seed_rows() -> list[dict]:
    try:
        raw = resources.files("paleditor").joinpath("data/items.json").read_text("utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        log.warning("no item seed file found; the catalogue starts empty")
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.error("item seed file is not valid JSON: %s", exc)
        return []
    rows = payload.get("items", []) if isinstance(payload, dict) else payload
    return [row for row in rows if isinstance(row, dict) and row.get("item_id")]


def seed(conn: sqlite3.Connection) -> int:
    """Upsert the shipped catalogue.

    Rows an operator marked 'confirmed' are left alone: those were read back
    from a live dump and outrank anything shipped in the repo.
    """
    rows = seed_rows()
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO items(item_id, display_name, category, max_stack, provenance)
        VALUES (:item_id, :display_name, :category, :max_stack, 'seed')
        ON CONFLICT(item_id) DO UPDATE SET
            display_name = excluded.display_name,
            category     = excluded.category,
            max_stack    = excluded.max_stack,
            provenance   = 'seed'
        WHERE items.provenance != 'confirmed'
        """,
        [
            {
                "item_id": row["item_id"],
                "display_name": row.get("display_name") or row["item_id"],
                "category": row.get("category"),
                "max_stack": row.get("max_stack"),
            }
            for row in rows
        ],
    )
    conn.commit()
    return len(rows)


def record_observed(conn: sqlite3.Connection) -> int:
    """File every unknown id the world holds as 'observed'.

    That gives the curation job a worklist. An unnamed item still renders as
    its raw id in the UI rather than being hidden, and it still gets a
    category, so the slot grid stays readable without anyone naming anything.
    """
    unknown = [
        row["item_id"]
        for row in conn.execute(
            "SELECT DISTINCT s.item_id FROM slots s "
            "WHERE s.item_id IS NOT NULL "
            "AND s.item_id NOT IN (SELECT item_id FROM items)"
        )
    ]
    if unknown:
        conn.executemany(
            "INSERT INTO items(item_id, display_name, category, max_stack, provenance) "
            "VALUES (?, ?, ?, NULL, 'observed')",
            [
                (item_id, derive_display_name(item_id), categorise(item_id))
                for item_id in unknown
            ],
        )
    # Rows recorded before names were derived still carry their raw id.
    for row in conn.execute(
        "SELECT item_id FROM items WHERE provenance = 'observed' "
        "AND display_name = item_id"
    ).fetchall():
        conn.execute(
            "UPDATE items SET display_name = ? WHERE item_id = ?",
            (derive_display_name(row["item_id"]), row["item_id"]),
        )
    # Rows seeded before the category rules existed, or seeded without one,
    # would otherwise leave holes in the grid.
    for row in conn.execute(
        "SELECT item_id FROM items WHERE category IS NULL"
    ).fetchall():
        conn.execute(
            "UPDATE items SET category = ? WHERE item_id = ?",
            (categorise(row["item_id"]), row["item_id"]),
        )
    conn.commit()
    return len(unknown)
