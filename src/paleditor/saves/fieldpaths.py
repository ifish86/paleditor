"""Every save-format assumption paleditor makes, in one place.

Status legend:
  CONFIRMED  read back from a live dump of this server's world
  LIKELY     documented by the community parser, not yet checked here

All of the below were CONFIRMED on 2026-10-07 against
``.../0/BB377176C64B470FA2713A2BAF485854/Level.sav`` (5,617 map objects, 5,308
containers, 3 bases, 109 lockable objects of which 18 had a code set).

The structural keys are read through palworld-save-tools with its custom
decoders OFF, so each property is skipped by its declared size when paleditor
does not understand it. The byte layouts inside RawData live in blobs.py.
"""

from __future__ import annotations

# --- Top-level containers ------------------------------------------ CONFIRMED
MAP_OBJECT_KEY = "MapObjectSaveData"          # .value.values -> list of objects
ITEM_CONTAINER_KEY = "ItemContainerSaveData"  # .value -> list of {key, value}
BASE_CAMP_KEY = "BaseCampSaveData"            # .value -> list of {key, value}

# --- ConcreteModel module map keys --------------------------------- CONFIRMED
# ModuleMap is keyed by the full enum string, not a short name. Counts observed
# in the reference world are given for scale.
MODULE_ITEM_CONTAINER = "EPalMapObjectConcreteModelModuleType::ItemContainer"   # 2773
MODULE_PASSWORD_LOCK = "EPalMapObjectConcreteModelModuleType::PasswordLock"     # 109
MODULE_GUILD_SECURITY = "EPalMapObjectConcreteModelModuleType::GuildSecurity"   # 112

# --- Grouping ------------------------------------------------------ CONFIRMED
# Map objects carry no explicit parent base link, so chests are grouped by
# horizontal distance to the nearest base camp. 6000 is the value palstats
# settled on against this same world; measuring in 2D matters because a chest
# on an upper floor is still at that base.
BASE_ASSIGNMENT_RADIUS = 6000.0

# --- Object classification ----------------------------------------- CONFIRMED
#
# The distinction that matters most for the UI. The reference world holds 5,617
# map objects and ~2,700 containers, but only ~65 are chests anybody placed.
# Everything else is world loot that respawns, and listing it would bury the
# chests people actually want to find.

# Player-built storage. These are what paleditor browses and edits.
STORAGE_PREFIXES = (
    "ItemChest",        # ItemChest, ItemChest_02, ItemChest_03
    "Container01",      # Container01_Iron
    "Shelf",            # Shelf04_Iron and friends
    "DeathPenaltyChest",
)

# World loot. Tracked so it can be counted and filtered, never browsed by
# default: these churn constantly as the world respawns them.
LOOT_PREFIXES = (
    "TreasureBox",
    "CommonDropItem",
)

# Containers that belong to a production or feeding station rather than storage.
STATION_PREFIXES = (
    "PalFoodBox", "PalMedicineBox", "Factory", "SphereFactory", "BlastFurnace",
    "Crusher", "FlourMill", "WorkBench", "CampFire", "HatchingPalEgg",
    "PalBox",
)


def classify(object_type: str | None) -> str | None:
    """Return 'storage', 'loot', 'station', or None for everything else."""
    if not object_type:
        return None
    for prefix in STORAGE_PREFIXES:
        if object_type.startswith(prefix):
            return "storage"
    for prefix in LOOT_PREFIXES:
        if object_type.startswith(prefix):
            return "loot"
    for prefix in STATION_PREFIXES:
        if object_type.startswith(prefix):
            return "station"
    return None


def status_report() -> dict[str, str]:
    """Printed by ``paleditor verify-save``."""
    return {
        "container magic (PlM/PlZ)": "CONFIRMED",
        "GVAS structure, decoders off": "CONFIRMED",
        "container GUID": "CONFIRMED (ItemContainer module RawData[:16])",
        "lock code": "CONFIRMED (PasswordLock module RawData)",
        "slot layout": "CONFIRMED (i32 index, i32 count, fstring id, tail)",
        "empty slots are not stored": "CONFIRMED",
        "base camp coordinates": "CONFIRMED",
        "writing PlZ over a PlM world": "UNVERIFIED - run 'paleditor check-write'",
    }
