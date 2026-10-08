# Save file internals

Everything paleditor reads and writes lives in `Level.sav`. `Players/<UID>.sav`
holds identity, technology points and unlocked recipes, and is not touched.

Everything below was read back from this server's world on 2026-10-07
(`.../0/BB377176C64B470FA2713A2BAF485854`): 5,617 map objects, 5,308
containers, 3 bases, 109 lockable objects of which 18 carry a code.

## The container: PlM, not PlZ

A `.sav` is a 12-byte header followed by a compressed GVAS payload:

    u32 uncompressed_size | u32 compressed_size | 3-byte magic | u8 save_type

Two magics exist. `PlZ` is zlib. **This server writes `PlM`, which is Oodle
Mermaid.** That matters more than anything else in this document:

- cheahjs' `palworld-save-tools` hardcodes `MAGIC_BYTES = b"PlZ"`. It cannot
  read this world at all — it fails on byte 8. The original proposal's "GVAS
  wrapped in zlib" was true of older Palworld, not of this server.
- Its writer always emits `PlZ`, so a naive fix would have written a zlib save
  over an Oodle world on the first maintenance window.

paleditor reads both containers. Oodle decompression goes through a `libooz.so`
build loaded by ctypes ([`saves/oodle.py`](../src/paleditor/saves/oodle.py));
the library is not vendored, because which build works depends on the host.

### Writing PlZ over a PlM world

paleditor writes `PlZ`. There is no working Oodle compressor available to write
`PlM` with: the open reimplementation is decompression-only, and the
`OodLZ_Compress` exported by some forks segfaults when called.

**Palworld loads a `PlZ` save in place of a `PlM` one.** Confirmed on a live
server in October 2026: a world rewritten with no edits, only the container
changed, started and played normally.

That is less of a leap than it sounds. The rewritten file only has to survive
being *loaded* once — the game holds the world in memory and writes its own
saves in its own format, so within a save cycle the file on disk is `PlM`
again. Nothing is being converted.

It stays gated per deployment by `[palworld] plz_write_confirmed`, because a
future game version could stop accepting it and the cost of being wrong is a
world that will not load. To earn it:

```bash
paleditor check-write -c /etc/paleditor/paleditor.toml
```

That rewrites the save with no edits at all, so the only difference is the
container, and verifies the GVAS payload survives byte for byte. Then load the
result and see whether the game accepts it — on a copy of the world, or at a
time you are happy to restore from the backup it just made.

## Parsing: structure from the library, blobs by hand

The GVAS payload parses with `palworld-save-tools`, but with its custom
decoders **disabled**:

```python
GvasFile.read(payload, PALWORLD_TYPE_HINTS, {})
```

With the stock decoders it raises `Warning: EOF not reached` in
`rawdata/character.py`: those decoders walk record layouts, and this world's
character layout has moved since the parser's last release in October 2024.
With them off, every property is skipped by its declared size, the whole
structure parses in **2.1 seconds**, and `g.write({})` round-trips **byte for
byte**.

That is the lesson palstats states outright and paleditor now follows: anchor
on stable markers and skip what you do not understand, rather than walking
record layouts that every game patch can move.

The handful of `RawData` blobs paleditor does need are decoded in
[`saves/blobs.py`](../src/paleditor/saves/blobs.py). Each preserves the trailing
bytes it does not understand, so a re-encode changes only the fields asked for.

| Blob | Layout |
| --- | --- |
| Item slot | `i32 slot_index` `i32 stack_count` `i32 strlen` `utf8+NUL` `tail` (52 bytes in 11,209 of 11,237 slots) |
| Password lock | `u8 version` `i32 strlen` `utf8+NUL` `tail`. `strlen == 0` means lockable with no code set |
| Item container id | first 16 bytes are the container GUID |
| Transform | 3 doubles immediately before the **first** `(1.0, 1.0, 1.0)` scale vector |

The transform detail is a trap worth keeping: a base camp blob holds a *second*
scale vector later on, preceded by what looks like a bounding box of
`(-170, 0, 170)`. Scanning backwards puts every base at the same point.

## When the parser meets something new

The parser's last release was October 2024. This world has moved since, and it
will keep moving. The most common way that surfaces is a scalar type inside a
map that upstream's `prop_value` does not know:

```
Unknown property value type: Int64Property
(.worldSaveData.LevelObjectRecoverPartySaveData.Value.PlayerLastUsedTimes.Value)
```

That is a map of player GUID to a 64-bit timestamp. It appeared in this world
between September and October 2026 — an older copy of the same save parses
without it — and that single unhandled type stopped the whole world loading.

Upstream's `prop_value` handles only StructProperty, EnumProperty,
NameProperty, IntProperty and BoolProperty.
[`saves/gvas_compat.py`](../src/paleditor/saves/gvas_compat.py) adds the
missing scalars to **both** the reader and the writer. Both, symmetrically: the
write path depends on `GvasFile.write` reproducing its input byte for byte, so
a type the reader understood and the writer did not would corrupt a world
rather than fail to load one.

Adding another is one line in `SCALAR_TYPES`, mapping the property name to the
archive primitive that reads and writes it. **Only add types whose wire format
is unambiguous** — fixed-width scalars and strings. That same table writes the
save back, so a wrong guess is written into the world rather than merely
misread. A type you are unsure of should keep raising.

### Why unknown properties are not just skipped

Every GVAS property carries a declared size, and skipping what you do not
understand is the right instinct — it is what makes palstats' reader robust.
It is not available here. The size does not mean "bytes from this point", and
what it excludes varies by type. A `MapProperty` holding one GUID-to-`Int64`
entry declares 32, not the 70 bytes its body occupies: the key and value type
names and the optional GUID sit outside the count. `property()` likewise
references the size exactly once, as `size - 4` for arrays, and reads an
optional GUID ahead of scalar payloads. Seeking by it would desync the stream, and a desynced parse produces a
plausible but wrong world that the write path would then save back over the
real one. A clean failure is strictly better, so the parser is extended rather
than skipped past.

## Only occupied slots are stored

This changes the edit model, so it is the easiest thing to get wrong.

`SlotNum` is the container's **capacity**. The `Slots` array holds **only
occupied slots** — across 4,000 containers checked, not one empty slot was
stored. So:

- a chest's capacity and its number of stored slots are different numbers
- putting an item into an empty slot means **adding** an entry, not editing one
- clearing a slot means **removing** the entry, and the slot then simply
  vanishes from the save

paleditor's API still presents a full grid of `capacity` slots, because that is
what the UI draws and what a queued edit needs to target, but the save itself
stays sparse. Verification treats an absent slot as success for a clear and
failure for a fill.

## Objects: chests, loot and doors

`ConcreteModel.ModuleMap` is keyed by the full enum string:

| Module key | Count here |
| --- | --- |
| `EPalMapObjectConcreteModelModuleType::ItemContainer` | 2,773 |
| `EPalMapObjectConcreteModelModuleType::GuildSecurity` | 112 |
| `EPalMapObjectConcreteModelModuleType::PasswordLock` | 109 |

**Most containers are not chests.** Of ~2,700, roughly 65 are player-built
storage; the rest are `TreasureBox*` world loot that respawns constantly. There
are 1,443 plain `TreasureBox` objects alone. Listing them would bury the chests
anybody actually wants, so [`fieldpaths.classify`](../src/paleditor/saves/fieldpaths.py)
sorts objects into `storage`, `loot`, `station` and `lock-only`, and the chest
screens show storage by default.

**Locked doors carry codes and hold nothing.** Of the 18 objects with a code
set, several are `Stone_DoorWall`, `Wooden_DoorWall` and `Glass_DoorWall`. They
own no container, so a model keyed purely on container GUID drops them — and
lock codes are what this app exists for. They are kept as `kind = 'lock-only'`
with a synthetic id derived from type and position.

## Container GUIDs are stable, so nicknames work

Compared across backup snapshots six days apart:

| | start | end | persisted |
| --- | --- | --- | --- |
| Player storage | 43 | 65 | 33 (the rest were built or dismantled) |
| Everything else | 1,024 | 2,708 | 921 |

Every GUID present in both snapshots carried an **identical** lock code. So the
container GUID is a sound join key for `chest_meta`, and nicknames survive
restarts.

## Never read the live Level.sav

The server rewrites `Level.sav` roughly every 30 seconds (the backup snapshots
are exactly 30s apart). A read landing mid-write returns a torn file whose
header still declares the old compressed length.

Two defences, both borrowed from palstats:

1. `container.validate()` refuses a file whose declared compressed length does
   not match what is present.
2. Ingest reads the newest **completed** snapshot from
   `<save_dir>/backup/world/<timestamp>/Level.sav` by default
   ([`savesource.py`](../src/paleditor/savesource.py)). Those files are closed,
   so they are safe at any time. The maintenance window still uses the live
   file, because by then the server is stopped.

## Item ids

Read from the live world, not guessed. The reference world holds 466 distinct
ids across all containers. Notable ones that are easy to get wrong:

| Guess that would be wrong | Actual |
| --- | --- |
| `Paldium` | `Pal_crystal_S` |
| `Ore` | `CopperOre`, `ManganeseOre` |
| `Bullet_Normal` | `Arrow`, `RoughBullet`, `HandgunBullet`, `RifleBullet`, `AssaultRifleBullet` |
| `Gunpowder` | `GunPowder2` |
| `Bone` | `bone` (lower case) |

Pal souls are `PalUpgradeStone`, `PalUpgradeStone2`, `PalUpgradeStone3`.

[`data/items.json`](../src/paleditor/data/items.json) carries no `max_stack`
values. The API rejects an edit whose stack exceeds a known cap, so a guessed
cap blocks a legitimate edit; the column stays empty until a real value is read
off the item. Ingest records every unknown id as `provenance = 'observed'`, and
an id absent from the catalogue still renders as its raw string.
