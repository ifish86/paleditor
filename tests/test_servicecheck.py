"""Checking an installed systemd unit against the config it will run with.

These exist because the unit repeats, as literal paths, things the config
already states. When they drift, systemd fails at namespace setup and names
the missing path without saying why paleditor wanted it:

    Failed to set up mount namespacing: /home/.../SaveGames: No such file ...
    status=226/NAMESPACE
"""

from __future__ import annotations

from pathlib import Path

import pytest

from paleditor.servicecheck import PLACEHOLDER, check, parse_unit

UNIT_TEMPLATE = """\
[Unit]
Description=paleditor

[Service]
Type=simple
User={user}
WorkingDirectory=/opt/paleditor
ExecStart={exec_start}
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={rw}

[Install]
WantedBy=multi-user.target
"""


def write_unit(tmp_path: Path, *, rw: str, user: str = "root", exec_start: str | None = None) -> Path:
    import sys

    unit = tmp_path / "paleditor.service"
    unit.write_text(
        UNIT_TEMPLATE.format(
            user=user,
            rw=rw,
            exec_start=exec_start or f"{sys.executable} serve -c /etc/paleditor/paleditor.toml",
        )
    )
    return unit


def errors(findings):
    return [f.message for f in findings if f.level == "error"]


# -- parsing ---------------------------------------------------------------


def test_parses_only_the_service_section():
    directives = parse_unit(UNIT_TEMPLATE.format(user="x", rw="/a /b", exec_start="/bin/true"))
    assert directives["User"] == ["x"]
    assert directives["ReadWritePaths"] == ["/a /b"]
    assert "Description" not in directives


def test_ignores_comments_and_blank_lines():
    directives = parse_unit("[Service]\n# a comment\n\n; another\nUser=bob\n")
    assert directives == {"User": ["bob"]}


def test_collects_repeated_directives():
    directives = parse_unit("[Service]\nReadWritePaths=/a\nReadWritePaths=/b\n")
    assert directives["ReadWritePaths"] == ["/a", "/b"]


# -- the failure that actually happened ------------------------------------


def test_the_shipped_placeholder_is_reported(config, tmp_path):
    unit = write_unit(tmp_path, rw=f"/var/lib/paleditor {PLACEHOLDER}")
    found = errors(check(config, unit))
    assert any(PLACEHOLDER in m for m in found)


def test_a_missing_readwritepath_is_reported_with_the_reason(config, tmp_path):
    """This is the 226/NAMESPACE case: the path simply is not there."""
    missing = tmp_path / "Pal" / "Saved" / "SaveGames"
    unit = write_unit(tmp_path, rw=f"{tmp_path} {missing}")
    found = errors(check(config, unit))
    assert any("226/NAMESPACE" in m for m in found)
    assert any(str(missing) in m for m in found)


def test_an_unreadable_parent_is_a_warning_not_a_crash(config, tmp_path):
    """Path.exists() raises when a parent is unreadable, which is routine when
    the save lives under another user's home."""
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / "SaveGames").mkdir()
    blocked.chmod(0o000)
    try:
        unit = write_unit(tmp_path, rw=f"{tmp_path} {blocked / 'SaveGames'}")
        findings = check(config, unit)
        messages = [f.message for f in findings]
        assert any("not readable by this user" in m for m in messages)
    finally:
        blocked.chmod(0o700)


def test_a_save_directory_outside_readwritepaths_is_reported(config, tmp_path):
    other = tmp_path / "somewhere-else"
    other.mkdir()
    unit = write_unit(tmp_path, rw=str(other))
    found = errors(check(config, unit))
    assert any(str(config.palworld.save_dir) in m for m in found)


def test_a_correct_unit_passes(config, tmp_path):
    config.maintenance.backup_dir.mkdir(parents=True, exist_ok=True)
    unit = write_unit(
        tmp_path,
        rw=f"{config.palworld.save_dir} {config.database.path.parent} "
           f"{config.maintenance.backup_dir}",
    )
    assert errors(check(config, unit)) == []


def test_a_parent_directory_covers_its_children(config, tmp_path):
    """Granting the SaveGames parent is enough; the world dir sits inside it."""
    config.maintenance.backup_dir.mkdir(parents=True, exist_ok=True)
    unit = write_unit(tmp_path, rw=str(tmp_path))
    assert errors(check(config, unit)) == []


def test_an_optional_path_prefixed_with_a_dash_is_not_required(config, tmp_path):
    unit = write_unit(tmp_path, rw=f"{tmp_path} -/does/not/exist")
    found = errors(check(config, unit))
    assert not any("does/not/exist" in m for m in found)


def test_state_directory_counts_as_granted(tmp_path, friend_hash, owner_hash, save_dir):
    """StateDirectory=paleditor gives /var/lib/paleditor without listing it."""
    from paleditor.config import from_dict

    config = from_dict(
        {
            "server": {"listen": ["127.0.0.1:8080"]},
            "auth": {"password_hash": friend_hash, "owner_password_hash": owner_hash},
            "palworld": {"save_dir": str(save_dir), "save_backend": "fixture"},
            "maintenance": {"backup_dir": "/var/lib/paleditor/backups"},
            "database": {"path": "/var/lib/paleditor/paleditor.db"},
        },
        check_paths=False,
    )
    unit = tmp_path / "paleditor.service"
    unit.write_text(
        "[Service]\nProtectSystem=strict\nStateDirectory=paleditor\n"
        f"ReadWritePaths={save_dir}\nExecStart=/bin/true\n"
    )
    found = errors(check(config, unit))
    assert not any("/var/lib/paleditor" in m for m in found)


# -- other mismatches ------------------------------------------------------


def test_a_missing_unit_file_is_reported(config, tmp_path):
    found = errors(check(config, tmp_path / "absent.service"))
    assert any("does not exist" in m for m in found)


def test_a_missing_exec_start_binary_is_reported(config, tmp_path):
    unit = write_unit(
        tmp_path, rw=str(tmp_path), exec_start=str(tmp_path / "no-such-binary") + " serve"
    )
    found = errors(check(config, unit))
    assert any("ExecStart" in m and "does not exist" in m for m in found)


def test_an_unknown_user_is_reported_with_the_command_to_create_it(config, tmp_path):
    unit = write_unit(tmp_path, rw=str(tmp_path), user="definitely-not-a-user")
    found = errors(check(config, unit))
    assert any("useradd" in m for m in found)


def test_a_config_mismatch_is_only_a_warning(config, tmp_path):
    """The unit naming a different config than the one checked is worth saying,
    but it does not stop the service."""
    from dataclasses import replace as dc_replace

    config.maintenance.backup_dir.mkdir(parents=True, exist_ok=True)
    config = dc_replace(config, source_path=tmp_path / "mine.toml")
    unit = write_unit(tmp_path, rw=str(tmp_path))
    findings = check(config, unit)
    warnings = [f.message for f in findings if f.level == "warning"]
    assert any("this check ran against" in m for m in warnings)
    assert errors(findings) == []


def test_an_unhardened_unit_skips_the_path_coverage_checks(config, tmp_path):
    """Without ProtectSystem/ProtectHome there is no namespace to punch through."""
    unit = tmp_path / "paleditor.service"
    unit.write_text("[Service]\nUser=root\nExecStart=/bin/true\n")
    assert errors(check(config, unit)) == []


# -- reaching the save at all ----------------------------------------------


def test_a_locked_down_home_blocks_everything_beneath_it(tmp_path):
    """The real failure: /home/palworld at 0700.

    Group membership does not help, because the group has no execute bit to
    traverse with, and nothing below it can grant what the walk never reaches.
    """
    import os

    from paleditor.servicecheck import _first_untraversable

    home = tmp_path / "palworld"
    deep = home / "palworld-server" / "Pal" / "Saved" / "SaveGames" / "0" / "WORLD"
    deep.mkdir(parents=True)

    # Let a foreign uid walk as far as the simulated home, so the blocker the
    # check reports is the home itself rather than a pytest temp directory.
    # Only directories this test actually owns; /tmp itself belongs to root.
    chain = [
        p for p in [tmp_path, *tmp_path.parents]
        if str(p).startswith("/tmp/") and p.stat().st_uid == os.getuid()
    ]
    original = {p: p.stat().st_mode & 0o7777 for p in chain}
    try:
        for p in chain:
            p.chmod(original[p] | 0o011)
        home.chmod(0o700)
        for sub in (home / "palworld-server",):
            sub.chmod(0o775)

        foreign_uid = os.getuid() + 1000
        blocker = _first_untraversable(deep, foreign_uid, groups=set())
        assert blocker is not None
        assert blocker[0] == home.resolve()
        assert blocker[1] == 0o700

        # Granting the group the execute bit is what unblocks it.
        home.chmod(0o710)
        assert _first_untraversable(deep, foreign_uid, groups={home.stat().st_gid}) is None
    finally:
        home.chmod(0o755)
        for p, mode in original.items():
            p.chmod(mode)


def test_the_owner_of_the_save_can_always_reach_it(tmp_path):
    import os

    from paleditor.servicecheck import _first_untraversable

    deep = tmp_path / "home" / "world"
    deep.mkdir(parents=True)
    (tmp_path / "home").chmod(0o700)
    assert _first_untraversable(deep, os.getuid(), groups=set()) is None


def test_root_is_never_blocked(tmp_path):
    from paleditor.servicecheck import _first_untraversable

    deep = tmp_path / "home" / "world"
    deep.mkdir(parents=True)
    (tmp_path / "home").chmod(0o000)
    try:
        assert _first_untraversable(deep, 0, groups=set()) is None
    finally:
        (tmp_path / "home").chmod(0o755)
