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

## Item names and the full item list

Both come from the game's own data. The dedicated server ships its
localisation tables even though it never renders any of them:

    Pal/Content/L10N/en/Pal/DataTable/Text/DT_ItemNameText_Common.uexp

Rows are keyed `ITEM_NAME_<ItemId>_TextData` and followed by the display
string, so reading it gives every item id the game knows and what the game
calls it — 1,997 of them on this server.

```bash
paleditor import-item-names -c /etc/paleditor/paleditor.toml
```

The pak is found by walking up from `save_dir`; `--pak` or `[palworld]
pak_file` override that. Nothing is committed: the table is read from the
operator's own install, the same arrangement as the Oodle library.

This matters twice over. The picker used to offer only the few hundred ids
that already existed somewhere in the world, so a chest could not be given
anything it had never held. And the hand-written names in `items.json` were
guesses, several of them wrong:

| id | guessed | the game says |
| --- | --- | --- |
| `Wood_Fine` | Quality Wood | Hardwood |
| `CopperOre` | Copper Ore | Ore |
| `Pal_crystal_S` | Pal Crystal | Paldium Fragment |
| `PalUpgradeStone2` | Pal Soul (M) | Medium Pal Soul |

Names read from the game outrank the seed file. A row marked `confirmed` -
checked by hand against a live dump - still wins over both.

### Without the game files

Where no table can be read, a name is derived from the id instead:

| in the save | shown |
| --- | --- |
| `SkillCard_ThrowRock` | Throw Rock Skill Card |
| `Blueprint_LaserRifle_2` | Laser Rifle Blueprint 2 |
| `ExpBoost_03` | EXP Boost 3 |
| `Meat_BerryGoat` | Berry Goat Meat |

The kind moves to the end, because internal ids lead with it and the game does
not, and a trailing tier number stays last. It is a fallback: run
`import-item-names` and the real strings replace it.

The internal id is shown under each name in the item picker. Two items can read
alike once their ids are prettified, and the id is what actually goes into the
save.

## Improving coverage

Name items in [`data/items.json`](../src/paleditor/data/items.json) and re-run.
`fetch-icons` prints what it could not resolve, so that list is the worklist.
Ingest also records every unseen id as `provenance = 'observed'` with its raw
id as the display name, which is exactly the set worth naming.
