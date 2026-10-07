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


def test_an_unset_lock_file_is_still_validated(
    friend_hash, owner_hash, save_dir, tmp_path
):
    """The default points at /var/lib/paleditor, which is usually not writable
    by a developer running check-config locally. It must be reported, not
    discovered when the first window crashes."""
    raw = base_raw(friend_hash, owner_hash, save_dir, tmp_path)
    raw["maintenance"].pop("lock_file", None)
    import os

    default_parent = MaintenanceConfig().lock_file.parent
    if default_parent.is_dir() and os.access(default_parent, os.W_OK | os.X_OK):
        pytest.skip(f"{default_parent} happens to be writable here")
    with pytest.raises(ConfigError, match="lock_file"):
        from_dict(raw)


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
