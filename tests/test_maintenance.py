"""The maintenance window: the only code path that can destroy data.

Every test here drives the real sequence with a fake systemd and a fake RCON,
against a JSON save it can read back afterwards.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paleditor import db, ingest
from paleditor.errors import WindowBusy
from paleditor.locking import FileLock
from paleditor.maintenance import run_window

from .conftest import FakeRcon, FakeServerControl, read_world, slot_of

EMPTY_CHEST = "aaaa0004-0000-0000-0000-000000000004"
FULL_CHEST = "aaaa0001-0000-0000-0000-000000000001"


def queue_edit(conn, container_guid, slot_index, item_id, stack_count, by="friend"):
    cursor = conn.execute(
        "INSERT INTO pending_edits(container_guid, slot_index, item_id, "
        "stack_count, requested_by, status, requested_at) "
        "VALUES (?, ?, ?, ?, ?, 'queued', ?)",
        (container_guid, slot_index, item_id, stack_count, by, db.utcnow()),
    )
    conn.commit()
    return int(cursor.lastrowid)


def status_of(conn, edit_id):
    return conn.execute(
        "SELECT status, error FROM pending_edits WHERE id = ?", (edit_id,)
    ).fetchone()


# -- the happy path --------------------------------------------------------


def test_a_queued_edit_lands_in_the_save_and_is_marked_applied(
    config, conn, ingested, save_dir, fake_control
):
    edit_id = queue_edit(conn, EMPTY_CHEST, 0, "Wood", 50)

    report = run_window(
        config, control=fake_control, rcon_factory=lambda: FakeRcon()
    )

    assert report.ok, report.error
    assert report.claimed == 1
    assert report.applied == 1
    assert report.failed == 0

    world = read_world(save_dir)
    slot = slot_of(world, EMPTY_CHEST, 0)
    assert slot["ItemId"]["value"]["StaticId"]["value"] == "Wood"
    assert slot["StackCount"]["value"] == 50

    row = status_of(conn, edit_id)
    assert row["status"] == "applied"
    assert row["error"] is None


def test_clearing_a_slot_empties_it(config, conn, ingested, save_dir, fake_control):
    edit_id = queue_edit(conn, FULL_CHEST, 1, None, 0)

    report = run_window(config, control=fake_control, rcon_factory=lambda: FakeRcon())

    assert report.applied == 1, report.error
    slot = slot_of(read_world(save_dir), FULL_CHEST, 1)
    assert slot["ItemId"]["value"]["StaticId"]["value"] == "None"
    assert slot["StackCount"]["value"] == 0
    assert status_of(conn, edit_id)["status"] == "applied"


def test_the_sequence_saves_warns_stops_and_restarts(config, conn, ingested):
    queue_edit(conn, EMPTY_CHEST, 0, "Wood", 1)
    control = FakeServerControl(running=True)
    rcon_calls: list[str] = []

    object.__setattr__(config.maintenance, "shutdown_warning_seconds", 0)
    report = run_window(
        config, control=control, rcon_factory=lambda: FakeRcon(rcon_calls)
    )

    assert report.ok, report.error
    # RCON Save before anything else: the world in memory must hit disk first.
    assert rcon_calls[0] == "save"
    assert "stop" in control.calls
    assert "start" in control.calls
    assert control.calls.index("stop") < control.calls.index("start")
    assert report.server_restarted is True
    assert control.is_running() is True


def test_a_countdown_is_sent_when_one_is_configured(config, conn, ingested):
    queue_edit(conn, EMPTY_CHEST, 0, "Wood", 1)
    rcon_calls: list[str] = []
    object.__setattr__(config.maintenance, "shutdown_warning_seconds", 1)

    run_window(
        config,
        control=FakeServerControl(),
        rcon_factory=lambda: FakeRcon(rcon_calls),
    )

    assert any(call.startswith("shutdown 1 ") for call in rcon_calls)


def test_an_empty_queue_does_nothing_at_all(config, conn, ingested, fake_control):
    report = run_window(config, control=fake_control, rcon_factory=lambda: FakeRcon())
    assert report.claimed == 0
    assert report.applied == 0
    # The server is never stopped for a window with nothing to do.
    assert fake_control.calls == []
    assert fake_control.is_running() is True


# -- backups ---------------------------------------------------------------


def test_the_save_is_backed_up_with_a_checksum_before_the_write(
    config, conn, ingested, fake_control
):
    queue_edit(conn, EMPTY_CHEST, 0, "Wood", 1)
    run_window(config, control=fake_control, rcon_factory=lambda: FakeRcon())

    backups = sorted(config.maintenance.backup_dir.glob("Level-*.sav"))
    assert len(backups) == 1
    checksum = backups[0].with_suffix(".sav.sha256")
    assert checksum.is_file()
    digest = checksum.read_text().split()[0]
    assert len(digest) == 64

    from paleditor.saves.base import sha256_file

    assert sha256_file(backups[0]) == digest


def test_old_backups_are_pruned_to_the_configured_count(
    config, conn, ingested, fake_control
):
    assert config.maintenance.backup_count == 3
    for index in range(5):
        queue_edit(conn, EMPTY_CHEST, index, "Wood", index + 1)
        run_window(config, control=fake_control, rcon_factory=lambda: FakeRcon())
    backups = list(config.maintenance.backup_dir.glob("Level-*.sav"))
    assert len(backups) <= 3


# -- verification ----------------------------------------------------------


def test_an_edit_targeting_a_slot_that_no_longer_exists_fails_with_a_reason(
    config, conn, ingested, fake_control
):
    """The reverse-conflict case from the proposal.

    An edit queued hours earlier can target something that has since moved.
    Verification catches it and the edit is marked failed, not retried.
    """
    edit_id = queue_edit(conn, "no-such-container-guid", 0, "Wood", 10)

    report = run_window(config, control=fake_control, rcon_factory=lambda: FakeRcon())

    row = status_of(conn, edit_id)
    assert row["status"] == "failed"
    assert "not found in save" in row["error"]
    assert report.failed == 1
    # The server still comes back up.
    assert fake_control.is_running() is True


def test_a_failed_edit_keeps_its_error_and_is_not_retried(
    config, conn, ingested, fake_control
):
    queue_edit(conn, "no-such-container-guid", 0, "Wood", 10)
    run_window(config, control=fake_control, rcon_factory=lambda: FakeRcon())

    second = run_window(config, control=fake_control, rcon_factory=lambda: FakeRcon())
    assert second.claimed == 0, "a failed edit must not be picked up again"
    row = conn.execute("SELECT status, error FROM pending_edits").fetchone()
    assert row["status"] == "failed"
    assert row["error"]


def test_verification_reingests_so_the_ui_shows_the_new_contents(
    config, conn, ingested, fake_control
):
    queue_edit(conn, EMPTY_CHEST, 0, "Wood", 50)
    before = db.current_rev(conn)

    run_window(config, control=fake_control, rcon_factory=lambda: FakeRcon())

    assert db.current_rev(conn) > before
    row = conn.execute(
        "SELECT item_id, stack_count FROM slots "
        "WHERE container_guid = ? AND slot_index = 0",
        (EMPTY_CHEST,),
    ).fetchone()
    assert row["item_id"] == "Wood"
    assert row["stack_count"] == 50


# -- failure handling ------------------------------------------------------


def test_a_server_that_will_not_stop_aborts_before_touching_the_save(
    config, conn, ingested, save_dir
):
    """Step 3: the countdown is not trusted.

    A running server holds the world in memory and its next save would
    overwrite every edit, so the window must abort rather than write.
    """
    before = (save_dir / "Level.sav").read_bytes()
    edit_id = queue_edit(conn, EMPTY_CHEST, 0, "Wood", 50)

    class NeverStops(FakeServerControl):
        def stop(self) -> None:
            self.calls.append("stop")  # pretends to stop, stays running

    control = NeverStops(running=True)
    object.__setattr__(config.maintenance, "shutdown_timeout_seconds", 1)

    report = run_window(config, control=control, rcon_factory=lambda: FakeRcon())

    assert not report.ok
    assert "did not exit" in report.error
    assert (save_dir / "Level.sav").read_bytes() == before, "the save was modified"
    # Nothing was written, so the edit stays queued for the next window.
    assert status_of(conn, edit_id)["status"] == "queued"
    assert report.restored is False
    assert control.is_running() is True


def test_no_backup_directory_write_leaves_edits_queued(
    config, conn, ingested, save_dir, fake_control, monkeypatch
):
    before = (save_dir / "Level.sav").read_bytes()
    edit_id = queue_edit(conn, EMPTY_CHEST, 0, "Wood", 50)

    import paleditor.maintenance as mod

    def exploding_backup(*_args, **_kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(mod, "_backup", exploding_backup)

    report = run_window(config, control=fake_control, rcon_factory=lambda: FakeRcon())

    assert not report.ok
    assert (save_dir / "Level.sav").read_bytes() == before
    assert status_of(conn, edit_id)["status"] == "queued"
    assert fake_control.is_running() is True


def test_rcon_being_unreachable_falls_back_to_stopping_the_unit(
    config, conn, ingested, fake_control
):
    queue_edit(conn, EMPTY_CHEST, 0, "Wood", 50)

    report = run_window(
        config, control=fake_control, rcon_factory=lambda: FakeRcon(fail=True)
    )

    assert report.ok, report.error
    assert "stop" in fake_control.calls
    assert any("RCON unavailable" in step for step in report.steps)
    assert report.applied == 1


def test_a_server_that_fails_to_restart_is_reported_loudly(
    config, conn, ingested, save_dir
):
    """The worst outcome. The edit still lands, but the failure is recorded."""
    queue_edit(conn, EMPTY_CHEST, 0, "Wood", 50)
    control = FakeServerControl(running=True, start_fails=True)

    report = run_window(config, control=control, rcon_factory=lambda: FakeRcon())

    assert not report.ok
    assert "FAILED TO START" in report.error
    assert any("FAILED TO START" in step for step in report.steps)


def test_a_write_that_fails_before_the_rename_never_replaces_the_save(
    config, conn, ingested, save_dir, fake_control, monkeypatch
):
    """Serialisation blowing up must leave the original in place untouched.

    Nothing to restore here: the atomic rename never happened.
    """
    before = (save_dir / "Level.sav").read_bytes()
    edit_id = queue_edit(conn, EMPTY_CHEST, 0, "Wood", 50)

    from paleditor.saves.fixture import FixtureBackend

    def half_write(self, level_sav: Path, out_path: Path, edits):
        out_path.write_text("{}")
        raise OSError("disk died during serialise")

    monkeypatch.setattr(FixtureBackend, "apply_edits", half_write)

    report = run_window(config, control=fake_control, rcon_factory=lambda: FakeRcon())

    assert not report.ok
    assert (save_dir / "Level.sav").read_bytes() == before
    assert report.restored is False
    # Nothing was written, so the edit is still good for the next window.
    assert status_of(conn, edit_id)["status"] == "queued"
    assert fake_control.is_running() is True
    # The temp file must not be left behind next to the save.
    assert not list(save_dir.glob("*.tmp"))


def test_a_corrupt_write_is_detected_and_the_backup_restored(
    config, conn, ingested, save_dir, fake_control, monkeypatch
):
    """The restore path.

    A write that lands but produces a truncated save must be caught before the
    server restarts, and the backup put back.
    """
    before = (save_dir / "Level.sav").read_bytes()
    edit_id = queue_edit(conn, EMPTY_CHEST, 0, "Wood", 50)

    from paleditor.saves.fixture import FixtureBackend

    real_apply = FixtureBackend.apply_edits

    def truncating_apply(self, level_sav: Path, out_path: Path, edits):
        report = real_apply(self, level_sav, out_path, edits)
        out_path.write_text("{}")  # a valid file, but nothing like a world
        return report

    monkeypatch.setattr(FixtureBackend, "apply_edits", truncating_apply)

    report = run_window(config, control=fake_control, rcon_factory=lambda: FakeRcon())

    assert not report.ok
    assert "integrity check" in report.error
    assert report.restored is True
    assert (save_dir / "Level.sav").read_bytes() == before, "the world was not restored"
    # The batch is failed, not quietly requeued against a restored world.
    assert status_of(conn, edit_id)["status"] == "failed"
    assert fake_control.is_running() is True


def test_an_empty_write_is_caught_by_the_integrity_check(
    config, conn, ingested, save_dir, fake_control, monkeypatch
):
    before = (save_dir / "Level.sav").read_bytes()
    queue_edit(conn, EMPTY_CHEST, 0, "Wood", 50)

    from paleditor.saves.fixture import FixtureBackend

    real_apply = FixtureBackend.apply_edits

    def empty_apply(self, level_sav: Path, out_path: Path, edits):
        report = real_apply(self, level_sav, out_path, edits)
        out_path.write_text("")
        return report

    monkeypatch.setattr(FixtureBackend, "apply_edits", empty_apply)

    report = run_window(config, control=fake_control, rcon_factory=lambda: FakeRcon())

    assert not report.ok
    assert "empty" in report.error
    assert (save_dir / "Level.sav").read_bytes() == before


# -- lock contention -------------------------------------------------------


def test_a_second_window_is_refused_while_one_is_running(config, conn, ingested):
    with FileLock(config.maintenance.lock_file):
        with pytest.raises(WindowBusy, match="already running"):
            run_window(config, control=FakeServerControl())


def test_the_lock_is_released_after_a_run(config, conn, ingested, fake_control):
    run_window(config, control=fake_control, rcon_factory=lambda: FakeRcon())
    assert FileLock(config.maintenance.lock_file).is_locked() is False


def test_the_lock_is_released_even_when_the_window_fails(
    config, conn, ingested, monkeypatch
):
    queue_edit(conn, EMPTY_CHEST, 0, "Wood", 1)
    import paleditor.maintenance as mod

    monkeypatch.setattr(
        mod, "_backup", lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
    )
    run_window(config, control=FakeServerControl(), rcon_factory=lambda: FakeRcon())
    assert FileLock(config.maintenance.lock_file).is_locked() is False


def test_claiming_the_queue_marks_edits_applying_in_one_transaction(
    config, conn, ingested, fake_control
):
    """A second worker must not be able to pick up the same edits."""
    from paleditor.maintenance import _claim_queue

    queue_edit(conn, EMPTY_CHEST, 0, "Wood", 1)
    queue_edit(conn, EMPTY_CHEST, 1, "Stone", 2)

    first = _claim_queue(conn, "batch-one")
    assert len(first) == 2
    second = _claim_queue(conn, "batch-two")
    assert second == [], "the second claim must find nothing left"


def test_a_server_already_stopped_is_left_stopped(config, conn, ingested, save_dir):
    """The window must not start a server the operator had deliberately down."""
    queue_edit(conn, EMPTY_CHEST, 0, "Wood", 50)
    control = FakeServerControl(running=False)

    report = run_window(config, control=control, rcon_factory=lambda: FakeRcon())

    assert report.ok, report.error
    assert report.applied == 1
    assert "start" not in control.calls
    assert control.is_running() is False


def test_an_unknown_systemd_unit_stops_the_window_before_it_claims_anything(
    config, conn, ingested, save_dir
):
    """A typo in server_unit must not read as "the server is already stopped".

    systemctl is-active reports an unknown unit as inactive, so without this
    check the window would write the save under a live server and lose every
    edit to its next autosave.
    """
    before = (save_dir / "Level.sav").read_bytes()
    edit_id = queue_edit(conn, EMPTY_CHEST, 0, "Wood", 50)
    control = FakeServerControl(running=False, exists=False)

    report = run_window(config, control=control, rcon_factory=lambda: FakeRcon())

    assert not report.ok
    assert "server_unit" in report.error
    assert report.claimed == 0, "the queue must not be claimed"
    assert (save_dir / "Level.sav").read_bytes() == before
    assert status_of(conn, edit_id)["status"] == "queued"
    assert control.calls == []
