# paleditor

A read-mostly web app over a Palworld dedicated server's save file, with a
queued write path that applies edits only while the server is down.

Browse every chest on the server grouped by base, see settings and contents,
assign nicknames, and queue content edits that land during the nightly
maintenance window. See [docs/project_proposal.md](docs/project_proposal.md)
for the full design and
[docs/save-format.md](docs/save-format.md) for what is still unverified.

## Status

The backend, API, CLI and Quasar frontend are here, and the save layer has been
verified against the real server world: it reads the Oodle (`PlM`) container,
parses 5,617 map objects in ~2.3s, resolves lock codes and container contents,
and applies edits that survive a reparse.

Two things remain open:

- **Writing changes the container format, and that is unproven.** The world is
  `PlM` (Oodle); paleditor can only write `PlZ` (zlib), because no working
  Oodle compressor is available. The maintenance window **refuses to run** until
  `paleditor check-write` has been run and its output shown to load on a copy of
  the world. See [docs/save-format.md](docs/save-format.md).
The frontend has been built and driven headlessly against the real world: sign
in, browse bases, page through a chest list, open a 40-slot chest, queue an edit
and see it land in the queue. Node 20 and npm 9 are enough.

177 tests cover the config rules, the container and blob codecs, ingest, the
API, the RCON client, cron evaluation and the full maintenance sequence, none of
which need the game installed.

### Reading the save needs an Oodle decompressor

These saves use Oodle compression, which has no pure-Python decoder, so
paleditor loads a small native library through ctypes. It is not shipped here:
upstream declares no licence, so it is not ours to redistribute. Build it:

```bash
scripts/oodle/build.sh --save-dir "/path/to/SaveGames/0/<world-id>"
```

That fetches a pinned commit, compiles a decompressor, verifies it against your
save, and installs `lib/libooz.so`. See [docs/oodle.md](docs/oodle.md).

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
scripts/setup.sh --no-frontend
.venv/bin/python -m pytest
```

`setup.sh` rebuilds everything a clone does not carry: the virtualenv, the
Oodle decompressor and the web UI. By hand, if you prefer:

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

The web UI refreshes itself: slowly while idle, every few seconds while a
maintenance window is running, so the server going down, an edit moving to
`applying` and then to `applied`, and the window's result all appear without a
manual reload. It pauses while the tab is hidden.

`check-config` prints every path it resolved, including the maintenance lock
(which defaults to sitting beside the database) and the directory the running
code was imported from.

### If you moved or copied the project

**A virtualenv is not relocatable.** `.venv/bin/paleditor` has the original
interpreter's absolute path in its shebang, and an editable install records the
original `src/` directory in a `.pth` file. Copy a project with its `.venv`
into a new home and it keeps running the *old* source tree — so edits and
`git pull` appear to do nothing.

Check which code is actually running:

```bash
.venv/bin/paleditor --version
```

If that path is not inside the directory you are standing in, rebuild the venv:

```bash
cd ~/paleditor
rm -rf .venv
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/paleditor --version    # should now point at this directory
```

### Reaching the web UI

There is nothing separate to run. The API process serves the built SPA from
`frontend/dist/spa`, so once the service is up the UI is on the same addresses
as the API — every entry in `[server] listen`:

```
listening on 127.0.0.1:8080, 100.64.0.3:8080
```

Open the VPN address in a browser and sign in with the friend password, or the
owner password for the queue controls. On the VPS itself, or over an SSH
tunnel, `127.0.0.1:8080` works the same way.

The mount is decided when the process starts, so **after building the frontend,
restart the service** — otherwise it keeps serving the API-only placeholder.

### Working on the frontend

```bash
cd frontend
npm install
npx quasar dev     # proxies /api to 127.0.0.1:8080
npx quasar build   # output lands in frontend/dist/spa, which the API serves
```

`quasar.config.js` and `postcss.config.js` are CommonJS on purpose:
`@quasar/app-vite` v1 requires them with Node directly and the package is not
declared as an ES module. Everything under `src/` is ESM, which Vite handles.

## Commands

| Command | What it does |
| --- | --- |
| `paleditor serve` | Run the web app and the window scheduler |
| `paleditor ingest` | Parse the save into SQLite as a new rev |
| `paleditor run-window` | Run the maintenance window now; stops the game server |
| `paleditor hash-password` | Generate an argon2id hash for the config |
| `paleditor check-config` | Validate a config file and show the next window |
| `paleditor check-service` | Check the installed systemd unit against that config |
| `paleditor fetch-icons` | Download item icons from the wiki (partial; see [docs/icons.md](docs/icons.md)) |
| `paleditor verify-save` | Check the format assumptions against a real save |
| `paleditor dump-chest` | Print one chest's slots, to read item ids back |
| `paleditor check-write` | Rewrite a save unchanged, to test the PlZ container swap |
| `paleditor init-db` | Create the schema |

`-c/--config` works before or after the subcommand.

## The maintenance window

The only code path that modifies the world. One worker, one fixed sequence,
guarded by an exclusive lockfile so a scheduled and a manual run can never
overlap.

0. Refuse outright if writing would change the save's container format and
   that has not been confirmed, or if systemd does not recognise the game unit
1. Claim the queue in one transaction
2. RCON `Save`, then `Shutdown` with a countdown
3. **Confirm the process actually exited** — the countdown is not trusted,
   because a running server holds the world in memory and its next save would
   overwrite every edit
4. Back up `Level.sav` with a sha256, prune to `backup_count`
5. Apply the edits in memory, write a temp file, rename it into place
   atomically **carrying the original's owner and mode across**, then size-check
   the result and restore the backup if it looks corrupt
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
- `backup_dir`, the database directory, or an explicitly configured
  `lock_file` directory is not writable
- `rcon_password_file` is group- or world-readable
- `schedule` is not a valid five-field cron expression
- the maintenance lockfile's directory is not writable

## Keeping the data fresh

An ingest worker rereads the world every `[ingest] interval_seconds` (default
300), starting as soon as the service comes up. A pass whose save is
byte-identical to the last one read is skipped, so an idle world costs a
checksum rather than a parse.

This is not optional housekeeping: the maintenance window reingests only as its
verification step and returns early when the queue is empty, so without the
worker the database would only move when somebody ran `paleditor ingest` by
hand.

## Listening on more than one address

`listen` takes a list and every entry is bound by the one process, so the
loopback and VPN addresses in the example config are both served. A public
address still needs `allow_public = true`.

## Reading the right file

The server rewrites `Level.sav` roughly every 30 seconds, so reading it live can
return a torn file. Ingest reads the newest completed snapshot from
`backup/world/` instead, and the container header is validated before anything
is parsed. The maintenance window still uses the live file, because by then the
server is stopped.

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).
