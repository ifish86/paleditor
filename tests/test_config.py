"""The startup rules. Each of these refusals is a rule from the proposal."""

from __future__ import annotations

import pytest

from paleditor.config import MaintenanceConfig, from_dict, load
from paleditor.errors import ConfigError


def base_raw(friend_hash, owner_hash, save_dir, tmp_path, **overrides):
    raw = {
        "server": {"listen": ["127.0.0.1:8080"]},
        "auth": {"password_hash": friend_hash, "owner_password_hash": owner_hash},
        "palworld": {"save_dir": str(save_dir), "save_backend": "fixture"},
        "maintenance": {
            "backup_dir": str(tmp_path / "backups"),
            "lock_file": str(tmp_path / "window.lock"),
        },
        "database": {"path": str(tmp_path / "db" / "p.db")},
    }
    for section, values in overrides.items():
        raw.setdefault(section, {}).update(values)
    return raw


def test_accepts_a_sane_config(friend_hash, owner_hash, save_dir, tmp_path):
    config = from_dict(base_raw(friend_hash, owner_hash, save_dir, tmp_path))
    assert config.server.listen[0].host == "127.0.0.1"
    assert config.server.listen[0].port == 8080
    assert config.palworld.level_sav.name == "Level.sav"


# -- rule 1: do not bind a public address by accident ----------------------


@pytest.mark.parametrize("address", ["0.0.0.0:8080", "51.79.22.10:8080", "[::]:8080"])
def test_refuses_public_bind_without_allow_public(
    address, friend_hash, owner_hash, save_dir, tmp_path
):
    raw = base_raw(
        friend_hash, owner_hash, save_dir, tmp_path, server={"listen": [address]}
    )
    with pytest.raises(ConfigError, match="allow_public"):
        from_dict(raw)


def test_allows_public_bind_when_explicitly_enabled(
    friend_hash, owner_hash, save_dir, tmp_path
):
    raw = base_raw(
        friend_hash, owner_hash, save_dir, tmp_path,
        server={"listen": ["0.0.0.0:8080"], "allow_public": True},
    )
    assert from_dict(raw).server.allow_public is True


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1:8080",
        "10.0.0.5:8080",
        "192.168.1.9:8080",
        "172.16.0.1:8080",
        # The VPN range from the proposal's own example config. Python's
        # is_private reports False for this, so it needs an explicit allowance.
        "100.64.0.3:8080",
        "[::1]:8080",
        "[fd00::1]:8080",
    ],
)
def test_allows_loopback_private_and_vpn_addresses(
    address, friend_hash, owner_hash, save_dir, tmp_path
):
    raw = base_raw(
        friend_hash, owner_hash, save_dir, tmp_path, server={"listen": [address]}
    )
    assert from_dict(raw).server.listen


def test_refuses_a_hostname_that_could_resolve_anywhere(
    friend_hash, owner_hash, save_dir, tmp_path
):
    raw = base_raw(
        friend_hash, owner_hash, save_dir, tmp_path,
        server={"listen": ["paleditor.example.com:8080"]},
    )
    with pytest.raises(ConfigError, match="allow_public"):
        from_dict(raw)


@pytest.mark.parametrize("address", ["127.0.0.1", "127.0.0.1:abc", "127.0.0.1:99999"])
def test_refuses_malformed_listen_entries(
    address, friend_hash, owner_hash, save_dir, tmp_path
):
    raw = base_raw(
        friend_hash, owner_hash, save_dir, tmp_path, server={"listen": [address]}
    )
    with pytest.raises(ConfigError):
        from_dict(raw)


# -- rule 2: no default password, no first-run open window -----------------


@pytest.mark.parametrize("value", ["", "   ", "hunter2", "sha256:abc"])
def test_refuses_unset_or_plaintext_password(
    value, friend_hash, owner_hash, save_dir, tmp_path
):
    raw = base_raw(
        friend_hash, owner_hash, save_dir, tmp_path, auth={"password_hash": value}
    )
    with pytest.raises(ConfigError, match="password_hash"):
        from_dict(raw)


def test_refuses_identical_friend_and_owner_passwords(
    friend_hash, save_dir, tmp_path
):
    raw = base_raw(
        friend_hash, friend_hash, save_dir, tmp_path,
        auth={"password_hash": friend_hash, "owner_password_hash": friend_hash},
    )
    with pytest.raises(ConfigError, match="identical"):
        from_dict(raw)


def test_refuses_a_missing_auth_section(save_dir, tmp_path):
    raw = {
        "server": {"listen": ["127.0.0.1:8080"]},
        "palworld": {"save_dir": str(save_dir)},
    }
    with pytest.raises(ConfigError, match=r"\[auth\]"):
        from_dict(raw)


# -- rule 3: the save must actually be there -------------------------------


def test_refuses_a_save_dir_without_level_sav(
    friend_hash, owner_hash, tmp_path
):
    empty = tmp_path / "empty"
    empty.mkdir()
    raw = base_raw(
        friend_hash, owner_hash, empty, tmp_path, palworld={"save_dir": str(empty)}
    )
    with pytest.raises(ConfigError, match="Level.sav"):
        from_dict(raw)


def test_skip_paths_allows_checking_a_config_off_the_server(
    friend_hash, owner_hash, tmp_path
):
    missing = tmp_path / "nowhere"
    raw = base_raw(
        friend_hash, owner_hash, missing, tmp_path, palworld={"save_dir": str(missing)}
    )
    assert from_dict(raw, check_paths=False).palworld.save_dir == missing


# -- rule 4: the backup directory must be writable -------------------------


def test_refuses_an_unwritable_backup_dir(
    friend_hash, owner_hash, save_dir, tmp_path
):
    blocked = tmp_path / "ro" / "backups"
    blocked.parent.mkdir()
    blocked.parent.chmod(0o500)
    raw = base_raw(
        friend_hash, owner_hash, save_dir, tmp_path,
        maintenance={"backup_dir": str(blocked)},
    )
    try:
        with pytest.raises(ConfigError, match="backup_dir"):
            from_dict(raw)
    finally:
        blocked.parent.chmod(0o700)


def test_refuses_a_lock_file_in_an_unwritable_directory(
    friend_hash, owner_hash, save_dir, tmp_path
):
    """The window takes this lock first, so it must be writable at startup."""
    blocked = tmp_path / "ro2"
    blocked.mkdir()
    blocked.chmod(0o500)
    raw = base_raw(
        friend_hash, owner_hash, save_dir, tmp_path,
        maintenance={"lock_file": str(blocked / "sub" / "window.lock")},
    )
    try:
        with pytest.raises(ConfigError, match="lock_file"):
            from_dict(raw)
    finally:
        blocked.chmod(0o700)


def test_an_unset_lock_file_sits_beside_the_database(
    friend_hash, owner_hash, save_dir, tmp_path
):
    """The lock has to follow the configured state, not a fixed system path.

    It used to default to /var/lib/paleditor/maintenance.lock regardless of
    where the database lived, so a config that set every path it was asked for
    still failed validation on a path it was never told about.
    """
    raw = base_raw(friend_hash, owner_hash, save_dir, tmp_path)
    raw["maintenance"].pop("lock_file", None)
    config = from_dict(raw)
    assert config.maintenance.lock_file == config.database.path.parent / "maintenance.lock"


def test_an_explicit_lock_file_is_honoured(
    friend_hash, owner_hash, save_dir, tmp_path
):
    chosen = tmp_path / "elsewhere" / "window.lock"
    chosen.parent.mkdir()
    raw = base_raw(
        friend_hash, owner_hash, save_dir, tmp_path,
        maintenance={"lock_file": str(chosen)},
    )
    assert from_dict(raw).maintenance.lock_file == chosen


def test_the_readme_quick_start_config_validates(
    friend_hash, owner_hash, save_dir, tmp_path
):
    """The quick start sets save_dir, backup_dir and the database path, and
    nothing else. Following it verbatim must not hit a validation error."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    config = from_dict(
        {
            "server": {"listen": ["127.0.0.1:8080"]},
            "auth": {
                "password_hash": friend_hash,
                "owner_password_hash": owner_hash,
            },
            "palworld": {"save_dir": str(save_dir), "save_backend": "fixture"},
            "maintenance": {"backup_dir": str(tmp_path / "backups")},
            "database": {"path": str(db_dir / "paleditor.db")},
        }
    )
    assert config.maintenance.lock_file == db_dir / "maintenance.lock"


# -- rcon secret -----------------------------------------------------------


def test_refuses_a_world_readable_rcon_secret(
    friend_hash, owner_hash, save_dir, tmp_path
):
    secret = tmp_path / "rcon.secret"
    secret.write_text("hunter2")
    secret.chmod(0o644)
    raw = base_raw(
        friend_hash, owner_hash, save_dir, tmp_path,
        palworld={"rcon_password_file": str(secret)},
    )
    with pytest.raises(ConfigError, match="world-readable"):
        from_dict(raw)


def test_reads_a_locked_down_rcon_secret(
    friend_hash, owner_hash, save_dir, tmp_path
):
    secret = tmp_path / "rcon.secret"
    secret.write_text("hunter2\n")
    secret.chmod(0o600)
    raw = base_raw(
        friend_hash, owner_hash, save_dir, tmp_path,
        palworld={"rcon_password_file": str(secret)},
    )
    assert from_dict(raw).palworld.rcon_password() == "hunter2"


# -- schedule --------------------------------------------------------------


@pytest.mark.parametrize("schedule", ["0 5 * *", "not a cron", "0 5 * * * *"])
def test_refuses_a_malformed_schedule(
    schedule, friend_hash, owner_hash, save_dir, tmp_path
):
    raw = base_raw(
        friend_hash, owner_hash, save_dir, tmp_path,
        maintenance={"schedule": schedule},
    )
    with pytest.raises(ConfigError, match="schedule"):
        from_dict(raw)


def test_refuses_a_timeout_shorter_than_its_own_countdown(
    friend_hash, owner_hash, save_dir, tmp_path
):
    raw = base_raw(
        friend_hash, owner_hash, save_dir, tmp_path,
        maintenance={
            "shutdown_warning_seconds": 120,
            "shutdown_timeout_seconds": 60,
        },
    )
    with pytest.raises(ConfigError, match="shutdown_timeout_seconds"):
        from_dict(raw)


def test_refuses_unknown_sections(friend_hash, owner_hash, save_dir, tmp_path):
    raw = base_raw(friend_hash, owner_hash, save_dir, tmp_path)
    raw["frontend"] = {"theme": "dark"}
    with pytest.raises(ConfigError, match="unknown config section"):
        from_dict(raw)


def test_load_reports_a_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        load(tmp_path / "absent.toml")


def test_load_reports_invalid_toml(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text("[server\nlisten = nope")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load(path)


def test_refuses_an_rcon_secret_this_process_cannot_read(
    friend_hash, owner_hash, save_dir, tmp_path
):
    """0600 and readable are different questions.

    A secret owned by another user at 0600 is unreadable whatever group it
    carries, and 0600 is the only mode this validator accepts, so the file has
    to belong to whoever the service runs as. Documenting chown root:<group>
    produced a file the service could never read.
    """
    import os

    if os.geteuid() == 0:
        pytest.skip("root can read anything")
    secret = tmp_path / "rcon.secret"
    secret.write_text("hunter2")
    secret.chmod(0o000)  # 0600 for someone else looks like this from here
    raw = base_raw(
        friend_hash, owner_hash, save_dir, tmp_path,
        palworld={"rcon_password_file": str(secret)},
    )
    try:
        with pytest.raises(ConfigError, match="cannot read it"):
            from_dict(raw)
    finally:
        secret.chmod(0o600)


# -- paths this process cannot reach ---------------------------------------


def test_an_unreachable_save_reports_the_blocker_instead_of_crashing(
    friend_hash, owner_hash, tmp_path
):
    """pathlib raises PermissionError rather than returning False when an
    ancestor is not searchable.

    The game user's home is 0700 on a stock setup, so running paleditor as
    anyone else produced a PermissionError traceback out of pathlib instead of
    a sentence saying which directory was in the way.
    """
    import os

    if os.geteuid() == 0:
        pytest.skip("root can search anything")

    home = tmp_path / "palworld"
    save = home / "server" / "SaveGames" / "0" / "WORLD"
    save.mkdir(parents=True)
    (save / "Level.sav").write_text("{}")
    home.chmod(0o000)
    try:
        raw = base_raw(friend_hash, owner_hash, save, tmp_path,
                       palworld={"save_dir": str(save)})
        with pytest.raises(ConfigError) as caught:
            from_dict(raw)
        message = str(caught.value)
        assert "cannot search" in message
        assert str(home) in message, "the message must name the blocking directory"
        assert "chmod g+x" in message
    finally:
        home.chmod(0o755)


def test_a_genuinely_missing_save_still_reads_as_missing(
    friend_hash, owner_hash, tmp_path
):
    """Not every failure is a permission problem; absent must stay absent."""
    empty = tmp_path / "empty"
    empty.mkdir()
    raw = base_raw(friend_hash, owner_hash, empty, tmp_path,
                   palworld={"save_dir": str(empty)})
    with pytest.raises(ConfigError, match="does not contain a Level.sav"):
        from_dict(raw)


def test_the_blocking_ancestor_is_the_highest_one(tmp_path):
    import os

    from paleditor.config import _blocking_ancestor

    if os.geteuid() == 0:
        pytest.skip("root can search anything")

    outer = tmp_path / "outer"
    inner = outer / "inner" / "deep"
    inner.mkdir(parents=True)
    outer.chmod(0o000)
    try:
        assert _blocking_ancestor(inner / "file") == outer
    finally:
        outer.chmod(0o755)


def test_nothing_blocks_a_reachable_path(tmp_path):
    from paleditor.config import _blocking_ancestor

    reachable = tmp_path / "a" / "b"
    reachable.mkdir(parents=True)
    assert _blocking_ancestor(reachable / "file") is None
