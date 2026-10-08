"""Command line argument resolution.

These three commands are run during deployment, when the config already says
where the save and the Oodle library are. They used to require --save-dir and
reject -c, so the documented invocation failed with:

    paleditor: error: unrecognized arguments: -c /etc/paleditor/paleditor.toml
"""

from __future__ import annotations

from pathlib import Path

import pytest

from paleditor.cli import _build_parser, _save_inputs


def parse(argv):
    return _build_parser().parse_args(argv)


@pytest.mark.parametrize("command", ["verify-save", "dump-chest", "check-write"])
def test_the_save_commands_accept_a_config(command):
    args = parse([command, "-c", "/etc/paleditor/paleditor.toml"])
    assert args.config == Path("/etc/paleditor/paleditor.toml")


@pytest.mark.parametrize("command", ["verify-save", "dump-chest", "check-write"])
def test_a_config_before_the_subcommand_works_too(command):
    args = parse(["-c", "/etc/paleditor/paleditor.toml", command])
    assert args.global_config == Path("/etc/paleditor/paleditor.toml")


@pytest.mark.parametrize("command", ["verify-save", "dump-chest", "check-write"])
def test_save_dir_is_no_longer_required(command):
    """It comes from the config when not given."""
    assert parse([command]).save_dir is None


def test_check_write_no_longer_requires_an_output_path():
    assert parse(["check-write"]).out is None


def test_an_explicit_save_dir_wins_over_the_config(config, tmp_path):
    chosen = tmp_path / "elsewhere"
    chosen.mkdir()
    args = parse(["verify-save", "--save-dir", str(chosen)])
    args.config = config.source_path
    save_dir, _, _ = _save_inputs(args)
    assert save_dir == chosen


def test_the_config_supplies_the_save_dir_when_the_flag_is_absent(config, tmp_path):
    written = tmp_path / "from-config.toml"
    written.write_text(_render(config))
    args = parse(["verify-save", "-c", str(written)])
    save_dir, _, resolved = _save_inputs(args)
    assert save_dir == config.palworld.save_dir
    assert resolved is not None


def test_the_config_supplies_the_oodle_library(config, tmp_path):
    library = tmp_path / "libooz.so"
    library.write_bytes(b"\x7fELF")
    written = tmp_path / "with-oodle.toml"
    written.write_text(_render(config, oodle_library=library))
    args = parse(["verify-save", "-c", str(written)])
    _, oodle, _ = _save_inputs(args)
    assert oodle == library


def test_a_missing_config_and_no_save_dir_fails_with_a_sentence(tmp_path):
    """Not a traceback, and it says which flag would have covered it."""
    args = parse(["verify-save", "-c", str(tmp_path / "absent.toml")])
    with pytest.raises(SystemExit) as caught:
        _save_inputs(args)
    assert "--save-dir" in str(caught.value)


def test_an_explicit_save_dir_survives_an_unreadable_config(tmp_path):
    """A broken config must not block a command that was told everything."""
    chosen = tmp_path / "save"
    chosen.mkdir()
    args = parse([
        "verify-save", "-c", str(tmp_path / "absent.toml"), "--save-dir", str(chosen),
    ])
    save_dir, _, resolved = _save_inputs(args)
    assert save_dir == chosen
    assert resolved is None


def _render(config, oodle_library: Path | None = None) -> str:
    oodle = f'oodle_library = "{oodle_library}"\n' if oodle_library else ""
    return f"""
[server]
listen = ["127.0.0.1:8080"]

[auth]
password_hash = "{config.auth.password_hash}"
owner_password_hash = "{config.auth.owner_password_hash}"

[palworld]
save_dir = "{config.palworld.save_dir}"
save_backend = "fixture"
{oodle}
[maintenance]
backup_dir = "{config.maintenance.backup_dir}"
lock_file = "{config.maintenance.lock_file}"

[database]
path = "{config.database.path}"
"""
