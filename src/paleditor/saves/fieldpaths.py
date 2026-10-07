"""Every save-format assumption paleditor makes, in one place.

Phase 1 of the proposal is a verification job: confirm these paths against a
live dump before any UI is built on them. They are collected here, rather than
inlined across the parser, so that a format change is a one-file edit and so
``paleditor verify-save`` has something concrete to check.

Status legend:
  CONFIRMED  read back from a live dump on this world
  LIKELY     documented by the community parser, not yet checked here
  UNVERIFIED must be confirmed before the write path is trusted with it
"""

from __future__ import annotations

# --- Top-level containers -------------------------------------------- LIKELY
WORLD_SAVE_DATA = ("properties", "worldSaveData", "value")
MAP_OBJECT_SAVE_DATA = ("MapObjectSaveData", "value", "values")
ITEM_CONTAINER_SAVE_DATA = ("ItemContainerSaveData", "value")
BASE_CAMP_SAVE_DATA = ("BaseCampSaveData", "value")

# --- Map object ------------------------------------------------------- LIKELY
MAP_OBJECT_ID = ("MapObjectId", "value")
MAP_OBJECT_CONCRETE_MODEL = ("Model", "value")
MAP_OBJECT_TRANSFORM = ("WorldLocation", "value")
MAP_OBJECT_GUILD_ID = ("GroupIdBelongTo", "value")

# The container GUID a chest's inventory hangs off. Nicknames join on this, so
# its stability across a restart and a world save cycle is a Phase 1 check.
CONTAINER_ID_CANDIDATES = (
    ("Model", "value", "ModuleMap", "value", "ItemContainer", "value", "ContainerId", "value", "ID", "value"),
    ("Model", "value", "ItemContainerId", "value", "ID", "value"),
)

# --- Lock code ---------------------------------------------------- UNVERIFIED
#
# The field paleditor is least sure about. It sits on the map object's concrete
# model data rather than on the container, and it is the field most likely to
# move between game versions. The backend tries each candidate in order and
# reports which one hit; if none do, lock_codes_available goes false and the
# app runs in contents-only mode.
LOCK_CODE_CANDIDATES = (
    ("Model", "value", "ModuleMap", "value", "PasswordLock", "value", "Password", "value"),
    ("Model", "value", "PasswordLock", "value", "Password", "value"),
    ("Model", "value", "ModuleMap", "value", "Lock", "value", "Password", "value"),
)

# --- Item container --------------------------------------------------- LIKELY
CONTAINER_SLOTS = ("Slots", "value", "values")
SLOT_INDEX = ("SlotIndex", "value")
SLOT_ITEM_ID = ("ItemId", "value", "StaticId", "value")
SLOT_STACK_COUNT = ("StackCount", "value")

# --- Base camp -------------------------------------------------------- LIKELY
BASE_CAMP_ID = ("Id", "value")
BASE_CAMP_TRANSFORM = ("Transform", "value", "translation", "value")
BASE_CAMP_GUILD_ID = ("GroupIdBelongTo", "value")

# --- Grouping --------------------------------------------------------------
# Map objects carry no explicit parent base link, so chests are grouped by
# proximity to the nearest base camp with the guild id as a tiebreaker. Beyond
# this radius a chest is left unassigned rather than attached to a far base.
BASE_ASSIGNMENT_RADIUS = 7000.0

# Object type strings that paleditor treats as a browsable chest. Anything
# else with a container (a feed box, a palbox) is ignored by v1.
CHEST_OBJECT_TYPES = (
    "DeathPenaltyChest",
    "ItemChest",
    "DefenceOtomo",
    "Container",
)
CHEST_NAME_HINTS = ("chest", "box", "container", "cabinet")

# Checked before the name hints. These own containers but are not browsable
# chests, and "box" in CHEST_NAME_HINTS would otherwise pull them in.
CHEST_OBJECT_EXCLUDES = (
    "palbox",
    "feedbox",
    "foodbox",
    "trashbox",
    "medicinebox",
)


def status_report() -> dict[str, str]:
    """Used by ``paleditor verify-save`` to print what is still unconfirmed."""
    return {
        "worldSaveData containers": "LIKELY",
        "container GUID": "UNVERIFIED (Phase 1: survives restart?)",
        "lock code": "UNVERIFIED (Phase 1: dump a chest with a known code)",
        "slot ItemId / StackCount": "LIKELY",
        "pal soul item ids": "UNVERIFIED (Phase 1: place one of each size)",
        "base camp coordinates": "LIKELY",
    }
