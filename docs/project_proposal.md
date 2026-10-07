# paleditor: Project Proposal
 
Oct 6, 2026 · @Chris
 
## Summary
 
paleditor becomes a read-mostly web app over a Palworld dedicated server's save file, with a queued write path that applies edits only while the server is down.
 
Today it tracks chest lock codes. This proposal extends it to browse every chest on the server grouped by base, show each chest's settings and contents, let the owner assign nicknames, and queue content edits that are applied during a maintenance window.
 
Users are the server owner and roughly a dozen friends. The server runs on a rented OVHcloud VPS and already resets daily at about 5am, which gives the write path a natural window.
 
## Goals and non-goals
 
v1 ships when a friend can open the app, find a chest by nickname, see what is in it, and queue a fill that lands at the next reset without anyone touching the VPS by hand.
 
**In scope for v1**
 
- Browse chests grouped by base, with container GUID, coordinates and slot contents
- Show each chest's settings, including the lock code, subject to the verification in Save file internals
- Assign and edit nicknames, stored outside the save file
- Queue a slot edit (set item, set stack count, clear slot) against a chest
- Apply the queue automatically during the 5am reset, and on demand with a confirmation
- Keep rolling backups of `Level.sav` and verify each applied edit by reparsing
**Out of scope for v1**
 
- Editing pals, player inventories, guilds, technology points or base structures
- Any write path that does not go through the maintenance window
- Multi-server support; one world, one save directory
- Public access; the app sits behind auth and is for the owner and invited friends only
- Game Pass or Xbox save formats; Steam dedicated server saves only
## Architecture
 
The app never touches `Level.sav` directly. Reads come from a SQLite database that an ingest worker populates, and writes go into a queue that only the window worker drains.
 
&#91;embedded content: paleditor architecture · 6 components, 1 gated write path\]
 
The dashed path is the only one that modifies the world, and it runs only while the server is stopped. Everything the user sees is served from rows, so the UI responds in milliseconds despite the save file taking over a minute to parse.
 
## Save file internals
 
Everything paleditor reads and writes lives in `Level.sav`, not in the per-player saves. `Players/<UID>.sav` holds identity, technology points and unlocked recipes, and is not touched by this project.
 
`Level.sav` is a GVAS structure wrapped in zlib compression. The community parser is cheahjs' [palworld-save-tools](https://github.com/cheahjs/palworld-save-tools), whose last release was v0.24.0 in October 2024. Converting a world to JSON typically produces a file over 1GB, and convert.py takes roughly 1m40s per pass, so the parse cost drives the whole architecture.
 
The structures that matter, all under `worldSaveData`:
 
| Structure | What paleditor takes from it |
| --- | --- |
| `MapObjectSaveData` | One entry per placed object. Chests carry a container GUID, world coordinates and a guild id. |
| `ItemContainerSaveData` | Keyed by container GUID. Each entry holds the slot array: `SlotIndex`, `ItemId` (a static string such as `PalSphere`), `StackCount`. |
| `BaseCampSaveData` | Base GUIDs and world coordinates, used to group chests by base. |
 
Map objects do not carry an explicit parent base link. Group chests by proximity to the nearest base camp coordinate, with the guild id as a tiebreaker.
 
**Verify before building UI on it**
 
- [ ] Where the lock code actually lives. It sits on the map object's concrete model data rather than the container, and it is the field most likely to move between game versions. Dump one chest with a known code and confirm the path.
- [ ] The exact `ItemId` strings for pal souls. Place one of each size in a chest in-game, dump the container, and read the strings back. Do not guess them.
- [ ] Whether container GUIDs survive a server restart and a world save cycle. Nicknames depend on this.
- [ ] Which parser round-trips fastest on this world. Palworld Save Pal no longer converts JSON exported by the old `convert.py` back into a `.sav`, so pick one toolchain and stay on it.
## Data model
 
SQLite, one file, rebuilt on each ingest except for the tables the app owns. The container GUID is the join key throughout.
 
| Table | Columns | Owner |
| --- | --- | --- |
| `bases` | `base_guid`, `name`, `x`, `y`, `z`, `guild_id`, `last_seen_rev` | ingest |
| `chests` | `container_guid`, `base_guid`, `object_type`, `x`, `y`, `z`, `lock_code`, `slot_count`, `last_seen_rev` | ingest |
| `slots` | `container_guid`, `slot_index`, `item_id`, `stack_count` | ingest |
| `items` | `item_id`, `display_name`, `category`, `max_stack` | seeded, hand-curated |
| `chest_meta` | `container_guid`, `nickname`, `notes`, `updated_at` | app |
| `pending_edits` | `id`, `container_guid`, `slot_index`, `item_id`, `stack_count`, `requested_by`, `status`, `requested_at`, `applied_at`, `error` | app |
| `ingests` | `rev`, `started_at`, `finished_at`, `save_mtime`, `save_sha256`, `chest_count` | ingest |
 
Two decisions worth calling out.
 
Ingest writes a new `rev` rather than mutating rows in place. A chest that stops appearing keeps its last `last_seen_rev`, so the UI can show it as orphaned instead of having it vanish silently when someone dismantles it.
 
`chest_meta` and `pending_edits` are never derived from the save and never overwritten by ingest. They survive every reparse, which is what makes nicknames stable.
 
The `items` table is a curation job, not a parse job. Seed it with the item ids confirmed from live dumps and grow it as needed. An unknown `item_id` should render as the raw string rather than being hidden.
 
## API surface
 
FastAPI over the SQLite file. The frontend never sees a save file, a GVAS structure or a parse. Every response is small enough to render instantly.
 
| Method and path | Purpose |
| --- | --- |
| `GET /api/bases` | Bases with chest counts and last ingest time |
| `GET /api/bases/{base_guid}/chests` | Chests at one base, with nickname, lock code and fill level |
| `GET /api/chests/{container_guid}` | One chest: settings, all slots, nickname, notes, pending edits |
| `PATCH /api/chests/{container_guid}/meta` | Set nickname and notes |
| `POST /api/chests/{container_guid}/edits` | Queue a slot edit; returns the queued row |
| `DELETE /api/edits/{id}` | Cancel a queued edit that has not been applied |
| `GET /api/edits` | The queue, filterable by status |
| `GET /api/items` | Item catalog for the picker |
| `GET /api/status` | Server up or down, last ingest, next scheduled window, queue depth |
| `POST /api/maintenance/run` | Trigger the window now; owner only, requires confirmation |
 
Edit statuses are `queued`, `applying`, `applied`, `failed`, `cancelled`. A failed edit keeps its error text and stays visible rather than being retried silently.
 
Auth can stay simple. A single shared session cookie for the friend group plus an owner flag on the two destructive endpoints is proportionate to a dozen known users.
 
## Quasar frontend
 
Four screens, built mobile-first, since most lookups happen on a phone with the game running on another machine.
 
1. **Bases**. Cards per base, each showing chest count, how many are locked and how many have queued edits. Tapping a card opens its chest list.
2. **Chest list**. Grouped under the base, sorted by nickname then by coordinate. Each row shows nickname, lock code, fill level and a pending-edit badge. A search field filters across nickname, lock code and contained item.
3. **Chest detail**. The slot grid, each slot showing item icon or name and stack count. Tapping a slot opens the editor: pick an item, set a count, or clear. Nickname and notes are editable inline at the top.
4. **Queue and status**. The pending edit list with statuses, the last ingest time, the next window, and the owner-only button to run the window now.
The interaction that needs care is the edit confirmation. A queued edit does not take effect immediately, and that will surprise people. Every queued edit should show the time it is expected to land, and the chest detail should render pending changes as a visible overlay on the affected slots rather than as a separate list.
 
Use a Quasar `QTable` for the queue, `QCard` grid for bases, and a custom slot grid component for the chest detail. Nothing here needs a state library beyond Pinia for the session and the item catalog.
 
## Deployment and configuration
 
paleditor runs on the same VPS as the game server, as a systemd unit beside it. It needs read and write access to the save directory, permission to start and stop the server unit, and an RCON connection on localhost.
 
All settings live in one TOML file read at startup, with no secrets committed to the repo.
 
```toml
[server]
# One entry per interface to listen on.
# 127.0.0.1 is local only; a VPN address exposes it to the friend group.
listen = ["127.0.0.1:8080", "100.64.0.3:8080"]
allow_public = false   # must be true to bind 0.0.0.0 or a WAN address
 
[auth]
# Generate with: paleditor hash-password
password_hash = "$argon2id$v=19$m=65536,t=3,p=4$..."
owner_password_hash = "$argon2id$v=19$m=65536,t=3,p=4$..."
session_days = 30
 
[palworld]
save_dir = "/home/palworld/Pal/Saved/SaveGames/0/<world-id>"
server_unit = "palworld.service"
rcon_host = "127.0.0.1"
rcon_port = 25575
rcon_password_file = "/etc/paleditor/rcon.secret"
 
[maintenance]
schedule = "0 5 * * *"
shutdown_warning_seconds = 60
backup_dir = "/var/lib/paleditor/backups"
backup_count = 10
 
[database]
path = "/var/lib/paleditor/paleditor.db"
```
 
**Rules the loader enforces**
 
- Refuse to start if `listen` contains `0.0.0.0` or a non-loopback, non-private address while `allow_public` is false. The write path can modify the world, so exposing it by accident should be impossible.
- Refuse to start if `password_hash` is unset or looks like plaintext. No default password, no first-run open window.
- Refuse to start if `save_dir` does not contain a readable `Level.sav`, or if the process cannot write to `backup_dir`.
- Read the RCON password from its own file so the main config can be world-readable while the secret stays `0600`.
**Password handling**
 
Two passwords, both stored as argon2id hashes: one for the friend group and one for the owner. The owner password gates the two destructive endpoints, `POST /api/maintenance/run` and anything that cancels or edits another user's queued edit. A `paleditor hash-password` subcommand generates the hashes so nobody is tempted to paste plaintext.
 
Config is read once at startup. Changing a listen address or a password needs a service restart, which is the right trade for a single-operator deployment.
 
## Maintenance window and edit queue
 
The write path is the only part that can destroy data, so it is a single worker with a fixed sequence and no shortcuts. It runs on the 5am schedule and on demand.
 
1. Claim the queue. Mark `queued` edits as `applying` in one transaction so a second worker cannot pick them up.
2. Warn and stop. RCON `Save`, then `Shutdown 60 "paleditor maintenance"` so anyone online gets a countdown.
3. Confirm the process actually exited. Poll for the PID to disappear, with a hard timeout. Do not trust the countdown. A running server keeps the world in memory and its next save overwrites any edit.
4. Back up. Copy `Level.sav` to a timestamped file and record its sha256. Keep the last 10 and prune older ones.
5. Parse, apply, serialize. Apply every claimed edit to the in-memory structure, then write the new `Level.sav` to a temp path and move it into place atomically.
6. Start the server and wait for it to accept RCON.
7. Reingest and verify. Reparse the save, confirm each applied edit is present in the slot it targeted, and mark it `applied`. Anything that does not match is marked `failed` with its reason.
**Failure handling**
 
If any step before the write fails, abort and leave the edits `queued` for the next window. If the write itself fails, restore the most recent backup, start the server, and mark the batch `failed`. The worker should never leave the server stopped: a final `try/finally` that starts it is worth the ugliness.
 
**Lock contention**
 
Take an exclusive lockfile for the whole sequence. The scheduled run and a manual run must never overlap, and a manual run requested while a window is active should return a clear error rather than queuing a second worker.
 
## Build phases
 
Phase 1 is not optional and not parallelisable. Everything downstream assumes the field paths are known, and building UI on a guessed lock code path wastes the work twice.
 
&#91;embedded content: build phases · 5 phases, each with a gate\]
 
Phases 1 and 2 are where the project actually succeeds or fails. If the parse is too slow or the lock code is not recoverable, that is worth knowing in week one rather than after the frontend exists.
 
## Risks and unknowns
 
The largest risk is format drift, not code. Save tools break when Palworld changes its save format, and a game update can invalidate the parser between one reset and the next.
 
| Risk | Mitigation |
| --- | --- |
| Game update changes the save layout | Ingest validates its expectations and refuses to run on a mismatch. The app shows stale data with a warning rather than guessing. The write path refuses entirely until ingest passes. |
| Lock code field moves or is not where expected | Verify before building UI. Treat the field as optional throughout, so the app degrades to contents-only if it disappears. |
| Parser abandonment | cheahjs' tool last shipped v0.24.0 in October 2024. Keep the parser behind a thin interface so it can be swapped without touching ingest logic. |
| Edit corrupts the world | Timestamped backups with checksums, atomic replace, post-write verification, and a documented restore that is tested once before anyone else uses the app. |
| Parse time grows with world size | Measure on the real save early. If a full parse exceeds the window, move to an incremental or streaming reader before adding features. |
| Friends edit chests during the window | The server is down for the whole sequence, so this cannot happen. The risk is the reverse: an edit queued hours earlier targets a slot someone has since filled. Verification catches it and marks the edit failed. |
 
**Open questions**
 
- [ ] Does the VPS have enough RAM to hold the parsed world alongside the running server, or must ingest only run while the server is stopped?
- [ ] Should ingest run after every server save, or only at the window? Reading a save file while the server is writing it is a real hazard.
- [ ] Is read-only access for friends enough, or do they need to queue edits too? This changes the auth model significantly.
 
