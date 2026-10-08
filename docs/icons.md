# Item icons

Chest slots show an item's icon where one could be found, and a category colour
where one could not — which is most of them.

## Why coverage is partial

The wiki keys its images on **display names**. The save keys items on
**internal ids**:

| in the save | on the wiki |
| --- | --- |
| `Wood_Fine` | Quality Wood |
| `PalUpgradeStone2` | Pal Soul (M) |
| `Blueprint_AssaultRifle_Default2` | — |
| `SkillCard_ThrowRock` | — |

There is no transformation between the two. `paleditor fetch-icons` tries the
curated `display_name` first and a name derived from the id second, but a
derived name only works when the id happens to be ordinary English.

Measured against a live 470-item catalogue: **63 resolved, 403 did not**. Every
hit came from a curated name. Of the 403, a large share are blueprint and skill
card variants the wiki does not picture separately at all.

This improves as `data/items.json` is curated — each item given a real display
name becomes resolvable — but it will never reach everything, so nothing in the
UI depends on it.

## The fallback that does cover everything

Every item is sorted into a category from its id, and the slot grid colours
each cell accordingly. That is what makes a chest readable at a glance: a wall
of orange is ammunition, blue is blueprints, grey is raw material.

Classification is in
[`catalog.py`](../src/paleditor/catalog.py). Short keywords match whole words
only — `body` inside `Nobody`, `ring` inside `Spring` and `net` inside `Magnet`
all matched when it was plain substring search. Keywords of six characters or
more match anywhere, which is what lets run-together ids like `palsphere` and
`upgradestone` work.

## Fetching

```bash
paleditor fetch-icons -c /etc/paleditor/paleditor.toml
```

By default only items actually present in the world; `--all` covers the whole
catalogue, `--refresh` re-downloads. Icons land beside the database, in
`<database dir>/icons`, and the API serves them at
`/api/items/{item_id}/icon`.

Nothing downloaded is committed. The artwork belongs to the game's publisher;
this fetches it onto your own machine for your own server, the same arrangement
as the Oodle library. The fetcher identifies itself, batches lookups 40 at a
time and pauses between them, because it is someone else's wiki.

Fandom's CDN content-negotiates: a `.png` URL commonly returns WebP. The stored
format is taken from the file's own bytes, not its URL, and anything that is
not a real image — an error page, say — is discarded rather than saved.

## Item names

The picker and the slot grid show the game's wording, not the save's. For an
item nobody has curated, the name is derived from its id:

| in the save | shown |
| --- | --- |
| `SkillCard_ThrowRock` | Throw Rock Skill Card |
| `Blueprint_LaserRifle_2` | Laser Rifle Blueprint 2 |
| `ExpBoost_03` | EXP Boost 3 |
| `Meat_BerryGoat` | Berry Goat Meat |

The kind moves to the end, because internal ids lead with it and the game does
not, and a trailing tier number stays last. This is not the exact in-game
string — that lives in the game's localisation tables, and a dedicated server
build does not ship them; the paks reference `/Game/L10N/.../DT_*` but carry no
text, since a server renders none. A curated name in
[`data/items.json`](../src/paleditor/data/items.json) always wins, so anything
worth getting exactly right can be set there.

The internal id is shown under each name in the item picker. Two items can read
alike once their ids are prettified, and the id is what actually goes into the
save.

## Improving coverage

Name items in [`data/items.json`](../src/paleditor/data/items.json) and re-run.
`fetch-icons` prints what it could not resolve, so that list is the worklist.
Ingest also records every unseen id as `provenance = 'observed'` with its raw
id as the display name, which is exactly the set worth naming.
