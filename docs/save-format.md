# Save file internals, and what is still unverified

Everything paleditor reads and writes lives in `Level.sav`. `Players/<UID>.sav`
holds identity, technology points and unlocked recipes, and this project does
not touch it.

`Level.sav` is a GVAS structure wrapped in zlib compression. The community
parser is cheahjs'
[palworld-save-tools](https://github.com/cheahjs/palworld-save-tools), whose
last release was v0.24.0 in October 2024. Converting a world to JSON typically
produces a file over 1GB and takes roughly 1m40s per pass, which is why reads
are served from SQLite and never from a parse.

## Where the assumptions live

Every field path is collected in
[`src/paleditor/saves/fieldpaths.py`](../src/paleditor/saves/fieldpaths.py),
not inlined across the parser, so a format change is a one-file edit. Each is
labelled:

| Status | Meaning |
| --- | --- |
| `CONFIRMED` | Read back from a live dump on this world |
| `LIKELY` | Documented by the community parser, not yet checked here |
| `UNVERIFIED` | Must be confirmed before the write path is trusted with it |

**Nothing in this repo is `CONFIRMED` yet.** The build was done without access
to a real save.

## Phase 1: the checks to run before trusting the UI

Run these on the VPS, against the real world. Phase 1 is not optional and not
parallelisable: building UI on a guessed lock-code path wastes the work twice.

```bash
paleditor verify-save --save-dir /home/palworld/Pal/Saved/SaveGames/0/<world-id>
```

That prints the base and chest counts, whether a lock code resolved, a sample
code, every distinct item id found, and the status table above.

### 1. Where the lock code actually lives

The field most likely to move between game versions. It sits on the map
object's concrete model data rather than on the container. `verify-save` tries
each path in `LOCK_CODE_CANDIDATES` in order and reports which one hit.

Set a known code on a chest in-game, then confirm the value comes back:

```bash
paleditor dump-chest --save-dir <dir> <container-guid>
```

If none of the candidates resolve, `lock_codes_available` goes false, the API
reports it, and the UI drops to contents-only rather than showing every chest
as unlocked. Treat the field as optional throughout; it already is in the
schema, the records and the frontend.

### 2. The exact `ItemId` strings for pal souls

Deliberately absent from
[`src/paleditor/data/items.json`](../src/paleditor/data/items.json). These are
the ids most often wrong in community lists, and the proposal says not to guess
them. Place one of each size in a chest, dump the container, and add what comes
back with `provenance: "confirmed"` — the seeder never overwrites those.

Ingest records every unknown id it sees as `provenance: 'observed'`, so the
catalogue grows its own worklist. An id absent from the catalogue still renders
as its raw string rather than being hidden.

### 3. Whether container GUIDs survive a restart

Nicknames join on the container GUID, so if it changes, every nickname detaches
from its chest. Run `verify-save` twice with a server restart and a world save
cycle in between, and diff the GUID lists:

```bash
paleditor dump-chest --save-dir <dir> | awk '{print $1}' | sort > /tmp/before
# restart the server, let it save
paleditor dump-chest --save-dir <dir> | awk '{print $1}' | sort > /tmp/after
diff /tmp/before /tmp/after
```

If they drift, `chest_meta` needs a different join key and that is a schema
change, so check this early.

### 4. Which parser round-trips fastest

Palworld Save Pal no longer converts JSON exported by the old `convert.py` back
into a `.sav`, so pick one toolchain and stay on it. paleditor keeps the parser
behind the `SaveBackend` protocol in
[`src/paleditor/saves/base.py`](../src/paleditor/saves/base.py) so it can be
swapped without touching ingest or the window worker.

## The cheahjs backend is written but unrun

[`src/paleditor/saves/cheahjs.py`](../src/paleditor/saves/cheahjs.py) follows
the documented v0.24.0 API — `decompress_sav_to_gvas`, `GvasFile.read`,
`gvas.write`, `compress_gvas_to_sav` — but has never been executed against a
real save, because the dependency was not installed during the build. Install
it and run `verify-save` before relying on it:

```bash
pip install -e '.[parser]'
```

The import is guarded: without the parser the API, the frontend and the test
suite all work, and only ingest and the write path refuse.

## Structures used

All under `worldSaveData`:

| Structure | What paleditor takes from it |
| --- | --- |
| `MapObjectSaveData` | One entry per placed object. Chests carry a container GUID, world coordinates and a guild id. |
| `ItemContainerSaveData` | Keyed by container GUID. Each entry holds the slot array: `SlotIndex`, `ItemId`, `StackCount`. |
| `BaseCampSaveData` | Base GUIDs and coordinates, used to group chests by base. |

Map objects carry no explicit parent base link, so chests are grouped by
proximity to the nearest base camp with the guild id as a tiebreaker. Beyond
`BASE_ASSIGNMENT_RADIUS` a chest is left unassigned rather than attached to a
base it does not belong to; the UI surfaces those under a "Not near a base"
card instead of hiding them.

`CHEST_OBJECT_EXCLUDES` exists because `box` is one of the chest name hints and
would otherwise pull in the Pal Box and feed boxes, which v1 does not browse.
