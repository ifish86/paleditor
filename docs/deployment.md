# Deployment

paleditor runs on the same VPS as the game server, as a systemd unit beside it.
It needs read and write access to the save directory, permission to start and
stop the game unit, and an RCON connection on localhost.

## Install

paleditor runs as the **game server's own user**. That is not laziness about
isolation; it is the only arrangement that works without extra machinery:

- The save normally lives in that user's home, which is `0700` on a stock
  setup. No other account can traverse into it, and permissions further down
  are irrelevant because the walk never gets that far.
- Whoever writes `Level.sav` owns the replacement. Running as anyone else
  leaves the save owned by the wrong user after the first maintenance window,
  and the game server can then no longer write its own save.

A separate service account is possible; see
[Using a separate service account](#using-a-separate-service-account) for the
two extra things it needs.

```bash
install -d -o palworld -g palworld /opt/paleditor /var/lib/paleditor
install -d -o palworld -g palworld /var/lib/paleditor/backups
install -d -o root -g palworld -m 750 /etc/paleditor

git clone git@github.com:ifish86/paleditor.git /opt/paleditor
cd /opt/paleditor
python3 -m venv .venv
.venv/bin/pip install -e '.[parser]'

# The Oodle decompressor. Not shipped: upstream declares no licence, so
# paleditor builds it rather than redistributing it. See docs/oodle.md.
scripts/oodle/build.sh --prefix /opt/paleditor/lib \
  --save-dir "/home/palworld/palworld-server/Pal/Saved/SaveGames/0/<world-id>"
```

Build the frontend, which the API then serves from `frontend/dist/spa`:

```bash
cd frontend && npm ci && npx quasar build
```

## After a fresh clone

Four things paleditor needs are built rather than committed, so a new clone —
or a directory deleted and re-cloned — has none of them:

| | |
| --- | --- |
| `.venv/` | the Python environment |
| `lib/libooz.so` | the Oodle decompressor ([docs/oodle.md](oodle.md)) |
| `frontend/node_modules`, `frontend/dist/spa` | the web UI |

`/etc/paleditor/paleditor.toml`, the secret and the systemd unit live outside
the repo, so those survive. Rebuild the rest with:

```bash
cd /opt/paleditor
scripts/setup.sh --save-dir "/home/palworld/palworld-server/Pal/Saved/SaveGames/0/<world-id>"
systemctl restart paleditor
```

### Who should own the checkout

The person who maintains it, not the service. paleditor only ever **reads**
`/opt/paleditor` — `ProtectSystem=strict` stops it writing there even if it
tried — while the database, backups and icons all live in `/var/lib/paleditor`.

A root-owned checkout means `git pull`, `npm run dev` and `quasar build` all
need `sudo`, and the dev server fails outright:

```
Error: EACCES: permission denied, open '/opt/paleditor/frontend/.quasar/app.js'
```

So hand it to yourself:

```bash
sudo chown -R "$USER" /opt/paleditor
```

Directories stay `755` and files `644`, so the service user can still read
everything it needs. `setup.sh` run as root now gives its artefacts back to the
checkout's owner for the same reason.

Commands that write the *state* directory still run as the service user:

```bash
sudo -u palworld /opt/paleditor/.venv/bin/paleditor ingest -c /etc/paleditor/paleditor.toml
```

## Configure

```bash
cp config/paleditor.example.toml /etc/paleditor/paleditor.toml

# Two passwords: one for the friend group, one for the owner.
.venv/bin/paleditor hash-password      # -> auth.password_hash
.venv/bin/paleditor hash-password      # -> auth.owner_password_hash

# The RCON password lives in its own file so the main config can stay
# readable while the secret does not. It must be owned by the user the service
# runs as: paleditor insists on mode 0600, and at 0600 the group grants
# nothing, so a root-owned secret is one the service can never read.
printf '%s' 'your-rcon-password' > /etc/paleditor/rcon.secret
chown palworld:palworld /etc/paleditor/rcon.secret
chmod 600 /etc/paleditor/rcon.secret

.venv/bin/paleditor check-config -c /etc/paleditor/paleditor.toml
```

`check-config` refuses anything unsafe and prints the next scheduled window, so
run it before every restart.

## Permission to stop and start the game

The window needs `systemctl start/stop` on one unit, and nothing more. Grant
exactly that with a polkit rule rather than full sudo:

**`subject.user` must be the user the paleditor unit runs as**, not the name of
the game unit and not a service account you are no longer using. Getting it
wrong is not caught at startup: the window claims the queue, fails at the stop
with `Interactive authentication required`, requeues the edit and leaves the
server running. `paleditor check-service` reports it, and `/api/status` carries
it as a warning.

```javascript
// /etc/polkit-1/rules.d/50-paleditor.rules
polkit.addRule(function (action, subject) {
  if (action.id === "org.freedesktop.systemd1.manage-units" &&
      subject.user === "palworld") {
    var unit = action.lookup("unit");
    if (unit === "palworld.service") {
      var verb = action.lookup("verb");
      if (verb === "start" || verb === "stop" || verb === "status") {
        return polkit.Result.YES;
      }
    }
  }
  return polkit.Result.NOT_HANDLED;
});
```

## Run

The unit ships with a placeholder that **must** be replaced before it will
start. `ReadWritePaths` has to name the `SaveGames` directory holding your
world — the part of `save_dir` above the world id:

```bash
cp config/paleditor.service /etc/systemd/system/

SAVEGAMES=/home/palworld/palworld-server/Pal/Saved/SaveGames   # yours will differ
sed -i "s|/REPLACE-WITH-YOUR-SaveGames-DIRECTORY|$SAVEGAMES|" \
  /etc/systemd/system/paleditor.service
```

Then check the unit against your config before starting anything:

```bash
paleditor check-service -c /etc/paleditor/paleditor.toml
```

That compares the unit's `ReadWritePaths`, `ExecStart` and `User` against the
paths the config actually uses, and reports what would break. It exists
because systemd's own failure for this is opaque:

```
Failed to set up mount namespacing: /home/.../SaveGames: No such file or directory
Failed at step NAMESPACE spawning /opt/paleditor/.venv/bin/paleditor: No such file or directory
status=226/NAMESPACE
```

The second line is misleading: the binary is fine. systemd builds the mount
namespace before it runs anything, and **every path in `ReadWritePaths` must
already exist**, or the whole unit fails. The path it names in the first line
is the real problem.

Once `check-service` is clean:

```bash
systemctl daemon-reload
systemctl enable --now paleditor
journalctl -u paleditor -f
```

`ReadWritePaths` overrides `ProtectHome=read-only` for the paths it lists, so a
save directory under `/home` is fine.

### Listening on more than one address

Every entry in `listen` is bound by the one process, so the loopback and VPN
addresses in the example config are both served. A public address is still
refused unless `allow_public` is true.

If a bind fails — a typo'd address, a port already taken, a VPN interface that
has not come up yet — the service exits naming the address it could not bind,
rather than starting half-served.

On boot that can race: `network-online.target` does not mean a VPN interface
exists yet. `Restart=on-failure` with `RestartSec=5` means the unit retries
until the address appears, so it recovers on its own, but logs a failure each
time in between. To avoid the noise, order the unit after whatever brings the
interface up:

```ini
After=tailscaled.service
```

Reload polkit after editing a rule:

```bash
systemctl restart polkit
paleditor check-service -c /etc/paleditor/paleditor.toml
```

### Reaching the web UI

The API process serves the built frontend, so there is no second service. The
UI is on every address in `listen`. The mount is decided at startup, so after
building or rebuilding the frontend, restart the unit.

## Using a separate service account

Running paleditor under its own account is possible, but it needs two things
the default arrangement gets for free. `paleditor check-service` reports both.

1. **Traversal into the save.** A stock game user's home is `0700`, which stops
   every other account at the front door:

   ```bash
   chmod g+x /home/palworld          # group needs the execute bit to pass through
   usermod -aG palworld paleditor
   ```

   `g+x` without `g+r` is enough: it allows passing through the directory
   without allowing it to be listed.

2. **Ownership after a write.** The maintenance window replaces `Level.sav`
   with a new file owned by whoever wrote it. paleditor copies the original's
   owner and mode onto the replacement, but `chown` to another user requires
   root, so running as a non-root, non-owning account leaves the save belonging
   to the service and the game server unable to write it. Either make the save
   group-writable and accept that the group owns it, or do not use a separate
   account.

Then set `User=` and `Group=` in the unit accordingly and re-run
`paleditor check-service`.

## First ingest

Run it as the same user the service runs as. Running it as anyone else fails on
the save directory, because the game user's home is 0700 and nothing else can
search into it.

```bash
sudo -u palworld /opt/paleditor/.venv/bin/paleditor \
  -c /etc/paleditor/paleditor.toml ingest
```

Reading a save file while the server is writing it is a real hazard, which is
why ingest reads a completed snapshot from `backup/world/` rather than the live
file.

After this, the service keeps itself current: an ingest worker rereads the
world every `[ingest] interval_seconds`, skipping any pass whose save is
unchanged. The maintenance window is not what refreshes the data — it reingests
only to verify its own writes, and returns early when the queue is empty.

Measure the parse. If it exceeds the window, move to an incremental reader
before adding features.

## Restore from a backup

Test this once, before anyone else uses the app. The window writes a
timestamped copy and a checksum to `backup_dir` before every change.

```bash
systemctl stop palworld
cd /var/lib/paleditor/backups
ls -t Level-*.sav | head

# Verify the copy before trusting it.
sha256sum -c Level-20261007T050000Z.sav.sha256

SAVE=/home/palworld/Pal/Saved/SaveGames/0/<world-id>
cp -a "$SAVE/Level.sav" "$SAVE/Level.sav.broken"
cp Level-20261007T050000Z.sav "$SAVE/Level.sav"
chown palworld:palworld "$SAVE/Level.sav"

systemctl start palworld
```

Then reingest so the database matches what the world now holds:

```bash
sudo -u palworld /opt/paleditor/.venv/bin/paleditor \
  -c /etc/paleditor/paleditor.toml ingest
```

## Backing up what ingest cannot rebuild

`chest_meta` and `pending_edits` are app-owned: they are never derived from the
save and no reparse can reconstruct them. The rest of the database is
disposable.

```bash
sqlite3 /var/lib/paleditor/paleditor.db \
  ".dump chest_meta pending_edits" > /var/backups/paleditor-meta.sql
```

## What sharing the game's user does and does not give up

The service runs as `palworld`, so in principle it has that account's reach.
In practice the sandbox does the confining, not the uid:

- `ProtectSystem=strict` makes the entire filesystem read-only except what
  `ReadWritePaths` and `StateDirectory` name, so the service can **write** only
  `/var/lib/paleditor` and the `SaveGames` directory — not the server binaries,
  not the rest of the home.
- `NoNewPrivileges`, `PrivateTmp`, `RestrictSUIDSGID` and the kernel protections
  apply the same way whichever user it runs as.

What it does keep is **read** access to the game user's home, because
`ProtectHome=read-only` permits reads. If that matters to you, hide the parts
paleditor has no business seeing:

```ini
InaccessiblePaths=/home/palworld/Steam /home/palworld/.config /home/palworld/.local
```

Those are read-only already; this makes them absent from the service's view.
Add them to the unit and re-run `paleditor check-service` to confirm nothing it
needs got caught.

## RCON

The window uses RCON to save the world and give players a shutdown countdown
before stopping the server. It needs `RCONEnabled=True` in
`PalWorldSettings.ini`, and the secret in `/etc/paleditor/rcon.secret` must be
the server's **`AdminPassword`** — Palworld has no separate RCON password.

Without it the window still runs: it stops the unit directly instead, so
anyone online is disconnected without warning.

Palworld answers commands with RCON request id 0 rather than echoing the id it
was sent, which is a deviation from the Source protocol. paleditor treats ids
as advisory for that reason; a wrong password is still rejected.

## Operating notes

- **A game update that moves the save layout** shows as a failed ingest. The
  app keeps serving the last good rev with a warning banner, and the write path
  refuses until an ingest passes.
- **A window that fails** leaves its edits `queued` for the next one, unless
  the write itself landed, in which case the backup is restored and the batch
  is marked `failed` with the reason.
- **`window_in_progress`** in `/api/status` reflects the lockfile, so a manual
  run requested during the scheduled one gets a clear 409.
- **A failed edit is never retried silently.** It keeps its error text and
  stays visible in the queue.
