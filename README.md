# paleditor

A read-mostly web app over a Palworld dedicated server's save file, with a
queued write path that applies edits only while the server is down.

Browse every chest on the server grouped by base, see settings and contents,
assign nicknames, and queue content edits that land during the nightly
maintenance window. See [docs/project_proposal.md](docs/project_proposal.md)
for the full design and
[docs/save-format.md](docs/save-format.md) for what is still unverified.

## Status

This is an initial build. The backend, the API, the CLI and the Quasar
frontend are all here, and the write path is implemented end to end with
backups, an integrity check, atomic replace and post-write verification.

Two things are explicitly **not** done, and they are the two the proposal puts
in Phase 1:

- **The save-format field paths are unverified against a real world.** Every
  assumption is collected in
  [`src/paleditor/saves/fieldpaths.py`](src/paleditor/saves/fieldpaths.py) and
  marked `LIKELY` or `UNVERIFIED`. Run `paleditor verify-save` on the VPS
  before trusting any of it. In particular the lock-code path is a guess, and
  the app degrades to contents-only when it does not resolve.
- **The frontend has never been built or run.** It was written without Node
  available, so it is unexercised source. Expect to fix things on the first
  `quasar dev`.

The test suite covers the parts that do not need the game: 136 tests over the
config rules, extraction, ingest, the API, the RCON client, cron evaluation and
the full maintenance sequence, all against a JSON fixture world.

## Architecture

The app never touches `Level.sav` directly.

```
Level.sav ──> ingest worker ──> SQLite ──> FastAPI ──> Quasar SPA
                                  ^                       │
                                  │                       │ queue an edit
                                  └── window worker <──────┘
                                      (server down only)
```

Reads come from SQLite rows, so the UI responds in milliseconds despite a full
parse taking over a minute. Writes go into a queue that only the window worker
drains, and only while the game server is stopped.

## Quick start

Nothing below needs a Palworld server: the `fixture` backend reads a JSON world
instead of a compressed save.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

To drive the real app against the test world:

```bash
mkdir -p /tmp/pal/{save,backups,db}
cp tests/fixtures/world.json /tmp/pal/save/Level.sav

cat > /tmp/pal/paleditor.toml <<CONF
[server]
listen = ["127.0.0.1:8080"]

[auth]
password_hash = "$(.venv/bin/paleditor hash-password --stdin <<< friendpw)"
owner_password_hash = "$(.venv/bin/paleditor hash-password --stdin <<< ownerpw)"

[palworld]
save_dir = "/tmp/pal/save"
save_backend = "fixture"

[maintenance]
backup_dir = "/tmp/pal/backups"

[database]
path = "/tmp/pal/db/paleditor.db"
CONF

.venv/bin/paleditor -c /tmp/pal/paleditor.toml check-config
.venv/bin/paleditor -c /tmp/pal/paleditor.toml ingest
.venv/bin/paleditor -c /tmp/pal/paleditor.toml serve
```

The API is then on <http://127.0.0.1:8080>, with docs at `/docs`. Sign in with
`friendpw`, or `ownerpw` for the owner-gated endpoints.

### The frontend

```bash
cd frontend
npm install
npx quasar dev     # proxies /api to 127.0.0.1:8080
npx quasar build   # output lands in frontend/dist/spa, which the API serves
```

## Commands

| Command | What it does |
| --- | --- |
| `paleditor serve` | Run the web app and the window scheduler |
| `paleditor ingest` | Parse the save into SQLite as a new rev |
| `paleditor run-window` | Run the maintenance window now; stops the game server |
| `paleditor hash-password` | Generate an argon2id hash for the config |
| `paleditor check-config` | Validate a config file and show the next window |
| `paleditor verify-save` | **Phase 1.** Check the format assumptions against a real save |
| `paleditor dump-chest` | **Phase 1.** Print one chest's slots, to read item ids back |
| `paleditor init-db` | Create the schema |

`-c/--config` works before or after the subcommand.

## The maintenance window

The only code path that modifies the world. One worker, one fixed sequence,
guarded by an exclusive lockfile so a scheduled and a manual run can never
overlap.

1. Claim the queue in one transaction
2. RCON `Save`, then `Shutdown` with a countdown
3. **Confirm the process actually exited** — the countdown is not trusted,
   because a running server holds the world in memory and its next save would
   overwrite every edit
4. Back up `Level.sav` with a sha256, prune to `backup_count`
5. Apply the edits in memory, write a temp file, rename it into place
   atomically, then size-check the result and restore the backup if it looks
   corrupt
6. Start the server and wait for RCON
7. Reingest and verify each edit landed where it was aimed; mark it `applied`,
   or `failed` with the reason

A failure before the write leaves the edits `queued` for the next window. A
failure after it restores the newest backup. The server is started in a
`finally` block: the worker never leaves it down.

## Safety rules the config loader enforces

It refuses to start, rather than starting unsafely, when:

- `listen` contains `0.0.0.0`, a public address or a hostname while
  `allow_public` is false
- `password_hash` or `owner_password_hash` is unset, looks like plaintext, or
  the two are identical
- `save_dir` holds no readable `Level.sav`
- `backup_dir` or the database directory is not writable
- `rcon_password_file` is group- or world-readable
- `schedule` is not a valid five-field cron expression

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).
