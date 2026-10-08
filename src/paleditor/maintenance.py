"""The maintenance window: the only code path that modifies the world.

One worker, one fixed sequence, no shortcuts. It runs on the configured
schedule and on demand, and it holds an exclusive lockfile for the whole run so
a scheduled and a manual run can never overlap.

The sequence, from the proposal:

  1. Claim the queue in one transaction
  2. RCON Save, then Shutdown with a countdown
  3. Confirm the process actually exited
  4. Back up Level.sav with a checksum, prune old backups
  5. Parse, apply, serialise to a temp path, atomic rename
  6. Start the server, wait for RCON
  7. Reingest and verify every edit, mark applied or failed

A failure before the write leaves the edits queued for the next window. A
failure during the write restores the newest backup. The server is started in a
finally block: the worker must never leave it stopped.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import stat
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import db, ingest
from .config import Config
from .errors import MaintenanceError, RconError, SaveFormatError, WindowBusy
from .locking import FileLock
from .rcon import RconClient, wait_until_responsive
from .saves import SlotEdit, container
from .servercontrol import ServerControl, SystemdServerControl, wait_for_exit

log = logging.getLogger(__name__)

BACKUP_SUFFIX = ".sav"

# A written save outside this size band relative to its backup is treated as
# corrupt. Slot edits change a save's size by bytes, not by halves, so anything
# this far out means a truncated or garbage write. Checked before the server is
# restarted, because once it is up its next save overwrites the evidence.
MIN_SIZE_RATIO = 0.5
MAX_SIZE_RATIO = 2.0


@dataclass
class WindowReport:
    batch_id: str
    claimed: int = 0
    applied: int = 0
    failed: int = 0
    backup_path: Path | None = None
    restored: bool = False
    server_restarted: bool = False
    steps: list[str] = field(default_factory=list)
    error: str | None = None

    def step(self, message: str) -> None:
        log.info("[%s] %s", self.batch_id[:8], message)
        self.steps.append(message)

    @property
    def ok(self) -> bool:
        return self.error is None


def run_window(
    config: Config,
    *,
    trigger: str = "schedule",
    control: ServerControl | None = None,
    rcon_factory=None,
) -> WindowReport:
    """Run the full sequence. ``control`` and ``rcon_factory`` are injectable for tests."""
    batch_id = str(uuid.uuid4())
    report = WindowReport(batch_id=batch_id)
    control = control or SystemdServerControl(config.palworld.server_unit)

    lock = FileLock(config.maintenance.lock_file)
    try:
        lock.acquire()
    except WindowBusy:
        raise WindowBusy(
            "a maintenance window is already running; refusing to start a "
            "second worker"
        ) from None

    try:
        with db.closing_connect(config.database.path) as conn:
            result = _sequence(
                config, conn, report, control, rcon_factory, trigger=trigger
            )
        record_outcome(config, result)
        return result
    finally:
        lock.release()


def _sequence(
    config: Config,
    conn: sqlite3.Connection,
    report: WindowReport,
    control: ServerControl,
    rcon_factory,
    *,
    trigger: str,
) -> WindowReport:
    # Step 0a: refuse to change the save's container format unless that has
    # been shown to work on this server. Reading is PlM, writing is PlZ, and a
    # world the game then refuses to load is the worst outcome this code has.
    problem = _container_change_blocked(config)
    if problem is not None:
        report.error = problem
        report.step(problem)
        return report

    # Step 0: refuse a unit systemd does not know. A typo in server_unit reads
    # as "already stopped", and writing the save under a live server would lose
    # every edit to its next autosave.
    checker = getattr(control, "unit_exists", None)
    if checker is not None and not checker():
        report.error = (
            f"systemd does not know the unit {config.palworld.server_unit!r}. "
            "Refusing to run: paleditor cannot confirm the game server is down, "
            "and writing the save while it is up would lose every edit. Check "
            "[palworld] server_unit."
        )
        report.step(report.error)
        return report

    # Step 1: claim the queue. One transaction, so a second worker cannot pick
    # up the same edits even if the lockfile were somehow bypassed.
    edits = _claim_queue(conn, report.batch_id)
    report.claimed = len(edits)
    if not edits:
        report.step("no queued edits; nothing to do")
        return report
    report.step(f"claimed {len(edits)} edit(s) as batch {report.batch_id}")

    level = config.palworld.level_sav
    started_running = control.is_running()
    wrote_save = False

    try:
        # Step 2: warn and stop.
        if started_running:
            _warn_and_stop(config, control, report, rcon_factory)
            # Step 3: confirm it actually exited.
            if not wait_for_exit(
                control, timeout=config.maintenance.shutdown_timeout_seconds
            ):
                raise MaintenanceError(
                    "the server process did not exit within "
                    f"{config.maintenance.shutdown_timeout_seconds}s. Aborting "
                    "before touching the save: a running server holds the world "
                    "in memory and its next save would overwrite every edit."
                )
            report.step("confirmed the server process exited")
        else:
            report.step("server was already stopped")

        # Step 4: back up.
        backup = _backup(config, report)

        # Step 5: parse, apply, serialise, atomic rename.
        backend = ingest._backend_for(config)
        temp_path = level.with_name(level.name + f".paleditor-{report.batch_id[:8]}.tmp")
        try:
            apply_report = backend.apply_edits(level, temp_path, edits)
            _atomic_replace(temp_path, level)
            wrote_save = True
            report.step(f"wrote {level.name} with {len(apply_report.applied)} edit(s)")
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            if wrote_save:
                _restore(backup, level, report)
            raise MaintenanceError(
                f"applying edits failed: {exc}. The save was not replaced."
            ) from exc

        # Still before step 6: a cheap sanity check on what was just written.
        # A full reparse happens in step 7, but that runs after the server is
        # back up, which is too late to put a corrupt save back.
        problem = _integrity_problem(level, backup)
        if problem is not None:
            _restore(backup, level, report)
            report.restored = True
            raise MaintenanceError(
                f"the written save failed its integrity check ({problem}); "
                "restored the backup"
            )

        for edit_id, reason in apply_report.failed.items():
            _mark_failed(conn, edit_id, reason)
        report.failed = len(apply_report.failed)

    except Exception as exc:
        report.error = str(exc)
        if wrote_save and not report.restored:
            # The write landed but something after it failed: put the world back.
            _restore(_newest_backup(config), level, report)
            report.restored = True
            _fail_batch(conn, report.batch_id, f"write rolled back: {exc}")
        elif report.restored:
            _fail_batch(conn, report.batch_id, f"write rolled back: {exc}")
        else:
            # Nothing was written, so the edits are still valid. Requeue them
            # for the next window rather than failing them.
            _requeue_batch(conn, report.batch_id)
            report.step("left edits queued for the next window")
        log.error("maintenance window failed: %s", exc)
        return _finish(config, control, report, started_running, conn)

    return _finish(config, control, report, started_running, conn, verify=True)


def _finish(
    config: Config,
    control: ServerControl,
    report: WindowReport,
    started_running: bool,
    conn: sqlite3.Connection,
    *,
    verify: bool = False,
) -> WindowReport:
    """Step 6 and 7. Always starts the server, whatever happened above."""
    try:
        if started_running:
            control.start()
            report.server_restarted = True
            report.step(f"started {config.palworld.server_unit}")
            if config.palworld.rcon_password_file is not None:
                try:
                    responsive = wait_until_responsive(
                        config.palworld.rcon_host,
                        config.palworld.rcon_port,
                        config.palworld.rcon_password(),
                        timeout=180.0,
                    )
                    report.step(
                        "server is accepting RCON" if responsive
                        else "server started but RCON did not answer within 180s"
                    )
                except Exception as exc:  # never let this mask the outcome
                    report.step(f"could not confirm RCON after start: {exc}")
    except Exception as exc:
        # The ugliest branch, and the most important. A window that leaves the
        # server down is worse than a window that fails.
        message = f"FAILED TO START {config.palworld.server_unit}: {exc}"
        log.critical(message)
        report.step(message)
        report.error = (report.error + "; " if report.error else "") + message
        return report

    if verify:
        _verify(config, conn, report)
    return report


def _warn_and_stop(config, control, report, rcon_factory) -> None:
    warning = config.maintenance.shutdown_warning_seconds
    try:
        factory = rcon_factory or (
            lambda: RconClient(
                config.palworld.rcon_host,
                config.palworld.rcon_port,
                config.palworld.rcon_password(),
            )
        )
        with factory() as client:
            client.save()
            report.step("RCON Save")
            if warning > 0:
                client.shutdown(warning, "paleditor maintenance")
                report.step(f"RCON Shutdown with a {warning}s countdown")
    except (RconError, Exception) as exc:
        # RCON failing is not fatal: stop the unit directly instead. Anyone
        # online loses their countdown, which is better than a stuck window.
        report.step(f"RCON unavailable ({exc}); stopping the unit directly")
        control.stop()
        return
    if warning > 0:
        time.sleep(min(warning + 5, config.maintenance.shutdown_timeout_seconds))
    if control.is_running():
        report.step("countdown elapsed but the unit is still active; stopping it")
        control.stop()


# -- queue -----------------------------------------------------------------


LAST_WINDOW_KEY = "last_window"


def preflight(config: Config, control: ServerControl | None = None) -> str | None:
    """Why the window would refuse to run, or None if it would proceed.

    The window itself checks these, but it runs in the background, so a refusal
    there is invisible to whoever pressed the button. Checking up front lets
    the request fail with the reason instead of reporting a start that never
    happens. Both are cheap: a 12-byte header read and a systemctl query.
    """
    blocked = _container_change_blocked(config)
    if blocked is not None:
        return blocked
    control = control or SystemdServerControl(config.palworld.server_unit)
    checker = getattr(control, "unit_exists", None)
    if checker is not None:
        try:
            known = checker()
        except Exception:
            known = True  # cannot tell; let the window decide
        if not known:
            return (
                f"systemd does not know the unit {config.palworld.server_unit!r}, "
                "so paleditor cannot confirm the game server is down. Check "
                "[palworld] server_unit."
            )
    return None


def record_outcome(config: Config, report: "WindowReport") -> None:
    """Keep the last window's result where the UI can see it.

    Without this a window that refuses, or fails halfway, leaves nothing behind
    but a log line on the server.
    """
    import json

    payload = {
        "batch_id": report.batch_id,
        "finished_at": db.utcnow(),
        "ok": report.ok,
        "claimed": report.claimed,
        "applied": report.applied,
        "failed": report.failed,
        "restored": report.restored,
        "error": report.error,
        "steps": report.steps[-12:],
    }
    try:
        with db.closing_connect(config.database.path) as conn:
            db.set_state(conn, LAST_WINDOW_KEY, json.dumps(payload))
            conn.commit()
    except Exception:
        log.exception("could not record the window outcome")


def last_outcome(conn) -> dict | None:
    import json

    raw = db.get_state(conn, LAST_WINDOW_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _container_change_blocked(config: Config) -> str | None:
    """None when writing is safe, otherwise why it is not."""
    if config.palworld.plz_write_confirmed:
        return None
    level = config.palworld.level_sav
    try:
        header = level.open("rb").read(container.HEADER_SIZE)
    except OSError as exc:
        return f"cannot read the save header of {level}: {exc}"
    if len(header) < container.HEADER_SIZE:
        return f"{level} is too short to be a save"
    magic = header[8:11]
    if magic != container.MAGIC_OODLE:
        return None
    return (
        f"{level.name} uses the Oodle (PlM) container, and paleditor can only "
        "write zlib (PlZ). Applying an edit would change the container format "
        "of a live world. Run 'paleditor check-write', load the result on a "
        "copy of the world to confirm the game accepts it, then set "
        "[palworld] plz_write_confirmed = true."
    )


def _claim_queue(conn: sqlite3.Connection, batch_id: str) -> list[SlotEdit]:
    with db.transaction(conn):
        conn.execute(
            "UPDATE pending_edits SET status='applying', batch_id=? "
            "WHERE status='queued'",
            (batch_id,),
        )
    rows = conn.execute(
        "SELECT id, container_guid, slot_index, item_id, stack_count "
        "FROM pending_edits WHERE batch_id=? AND status='applying' ORDER BY id",
        (batch_id,),
    ).fetchall()
    return [
        SlotEdit(
            edit_id=row["id"],
            container_guid=row["container_guid"],
            slot_index=row["slot_index"],
            item_id=row["item_id"],
            stack_count=row["stack_count"],
        )
        for row in rows
    ]


def _requeue_batch(conn: sqlite3.Connection, batch_id: str) -> None:
    with db.transaction(conn):
        conn.execute(
            "UPDATE pending_edits SET status='queued', batch_id=NULL "
            "WHERE batch_id=? AND status='applying'",
            (batch_id,),
        )


def _fail_batch(conn: sqlite3.Connection, batch_id: str, reason: str) -> None:
    with db.transaction(conn):
        conn.execute(
            "UPDATE pending_edits SET status='failed', error=?, applied_at=? "
            "WHERE batch_id=? AND status='applying'",
            (reason, db.utcnow(), batch_id),
        )


def _mark_failed(conn: sqlite3.Connection, edit_id: int, reason: str) -> None:
    with db.transaction(conn):
        conn.execute(
            "UPDATE pending_edits SET status='failed', error=?, applied_at=? WHERE id=?",
            (reason, db.utcnow(), edit_id),
        )


# -- backup and restore ----------------------------------------------------


def _backup(config: Config, report: WindowReport) -> Path:
    from .saves.base import sha256_file

    backup_dir = config.maintenance.backup_dir
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = backup_dir / f"Level-{stamp}{BACKUP_SUFFIX}"
    shutil.copy2(config.palworld.level_sav, target)
    digest = sha256_file(target)
    target.with_suffix(target.suffix + ".sha256").write_text(
        f"{digest}  {target.name}\n", encoding="utf-8"
    )
    report.backup_path = target
    report.step(f"backed up to {target.name} (sha256 {digest[:12]}…)")
    _prune_backups(config, report)
    return target


def _prune_backups(config: Config, report: WindowReport) -> None:
    backups = sorted(
        config.maintenance.backup_dir.glob(f"Level-*{BACKUP_SUFFIX}"),
        key=lambda p: p.name,
        reverse=True,
    )
    for stale in backups[config.maintenance.backup_count :]:
        stale.unlink(missing_ok=True)
        stale.with_suffix(stale.suffix + ".sha256").unlink(missing_ok=True)
        report.step(f"pruned old backup {stale.name}")


def _newest_backup(config: Config) -> Path | None:
    backups = sorted(
        config.maintenance.backup_dir.glob(f"Level-*{BACKUP_SUFFIX}"),
        key=lambda p: p.name,
        reverse=True,
    )
    return backups[0] if backups else None


def _restore(backup: Path | None, level: Path, report: WindowReport) -> None:
    if backup is None or not backup.is_file():
        report.step("NO BACKUP AVAILABLE TO RESTORE; leaving the save as it is")
        return
    temp = level.with_name(level.name + ".restore.tmp")
    shutil.copy2(backup, temp)
    _atomic_replace(temp, level)
    report.step(f"restored {level.name} from {backup.name}")


def _integrity_problem(written: Path, backup: Path) -> str | None:
    """A quick structural check on the save just written.

    Deliberately not a parse: this runs while the server is down and a full
    parse takes over a minute. It only has to catch the failures that would
    make the world unloadable, which all show up as a wildly wrong file size.
    """
    try:
        new_size = written.stat().st_size
        old_size = backup.stat().st_size
    except OSError as exc:
        return f"cannot stat the save: {exc}"
    if new_size == 0:
        return "the written save is empty"
    if old_size == 0:
        return None
    ratio = new_size / old_size
    if ratio < MIN_SIZE_RATIO:
        return (
            f"the written save is {new_size} bytes against a backup of "
            f"{old_size}, a {ratio:.0%} ratio"
        )
    if ratio > MAX_SIZE_RATIO:
        return (
            f"the written save is {new_size} bytes against a backup of "
            f"{old_size}, a {ratio:.0%} ratio"
        )
    return None


def _atomic_replace(source: Path, target: Path) -> None:
    """Rename into place, carrying over the original's ownership and mode.

    The replacement is a brand new inode owned by whoever paleditor runs as,
    with that process's umask. The game server runs as a different user and has
    to keep writing this file, so without copying the original's uid, gid and
    mode across, the first maintenance window leaves a save the server can no
    longer write. fsync of the directory makes the swap survive a crash.
    """
    try:
        original = target.stat()
    except FileNotFoundError:
        original = None

    if original is not None:
        try:
            os.chmod(source, stat.S_IMODE(original.st_mode))
        except OSError as exc:
            log.warning("could not copy mode onto the new save: %s", exc)
        if (os.geteuid() == 0) or (
            original.st_uid == os.geteuid() and original.st_gid in os.getgroups()
        ):
            try:
                os.chown(source, original.st_uid, original.st_gid)
            except OSError as exc:
                log.warning("could not copy ownership onto the new save: %s", exc)
        else:
            current = os.stat(source)
            if (current.st_uid, current.st_gid) != (original.st_uid, original.st_gid):
                log.warning(
                    "the new %s will be owned by %s:%s rather than %s:%s; the game "
                    "server may be unable to write its own save. Run paleditor as a "
                    "user that can restore the original ownership.",
                    target.name, current.st_uid, current.st_gid,
                    original.st_uid, original.st_gid,
                )

    os.replace(source, target)
    dir_fd = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


# -- verification ----------------------------------------------------------


def _verify(config: Config, conn: sqlite3.Connection, report: WindowReport) -> None:
    """Step 7. Reparse and confirm each edit landed where it was aimed.

    An edit queued hours earlier may target a slot someone has since filled.
    Verification is what catches that, and such an edit is marked failed rather
    than retried silently.
    """
    try:
        ingest.run(config, conn=conn)
    except (SaveFormatError, Exception) as exc:
        report.step(f"reingest after the write failed: {exc}")
        _fail_batch(conn, report.batch_id, f"could not verify: {exc}")
        report.error = (report.error + "; " if report.error else "") + str(exc)
        return

    rows = conn.execute(
        "SELECT id, container_guid, slot_index, item_id, stack_count "
        "FROM pending_edits WHERE batch_id=? AND status='applying'",
        (report.batch_id,),
    ).fetchall()

    applied = 0
    for row in rows:
        actual = conn.execute(
            "SELECT item_id, stack_count FROM slots "
            "WHERE container_guid=? AND slot_index=?",
            (row["container_guid"], row["slot_index"]),
        ).fetchone()
        wanted_clear = row["item_id"] is None or row["stack_count"] <= 0
        if actual is None:
            # The save stores only occupied slots, so a cleared slot correctly
            # disappears. Absence is success for a clear and failure otherwise.
            if wanted_clear:
                with db.transaction(conn):
                    conn.execute(
                        "UPDATE pending_edits SET status='applied', applied_at=?, "
                        "error=NULL WHERE id=?",
                        (db.utcnow(), row["id"]),
                    )
                applied += 1
            else:
                _mark_failed(conn, row["id"], "slot not present after the write")
                report.failed += 1
            continue
        if wanted_clear:
            matched = actual["item_id"] is None and actual["stack_count"] == 0
        else:
            matched = (
                actual["item_id"] == row["item_id"]
                and actual["stack_count"] == row["stack_count"]
            )
        if matched:
            with db.transaction(conn):
                conn.execute(
                    "UPDATE pending_edits SET status='applied', applied_at=?, "
                    "error=NULL WHERE id=?",
                    (db.utcnow(), row["id"]),
                )
            applied += 1
        else:
            _mark_failed(
                conn,
                row["id"],
                f"verification mismatch: expected "
                f"{row['item_id']}x{row['stack_count']}, found "
                f"{actual['item_id']}x{actual['stack_count']}",
            )
            report.failed += 1

    report.applied = applied
    report.step(f"verified {applied} applied, {report.failed} failed")
