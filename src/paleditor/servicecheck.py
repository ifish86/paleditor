"""Check an installed systemd unit against the configuration it will run with.

The unit has to repeat, as literal paths, several things the config already
says: where the save lives, where the state directory is, which binary to run.
When those drift apart systemd fails at namespace setup, and the message it
gives names the missing path but nothing about why paleditor wanted it:

    Failed to set up mount namespacing: /home/.../SaveGames: No such file ...
    Failed at step NAMESPACE spawning ...: No such file or directory
    status=226/NAMESPACE

This turns that into a sentence, before the unit is ever started.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

from .config import Config

DEFAULT_UNIT = Path("/etc/systemd/system/paleditor.service")
PLACEHOLDER = "/REPLACE-WITH-YOUR-SaveGames-DIRECTORY"


@dataclass
class Finding:
    level: str  # "error" or "warning"
    message: str


def parse_unit(text: str) -> dict[str, list[str]]:
    """Collect the [Service] directives we care about.

    systemd allows a directive to appear more than once, and for most of these
    a repeat appends rather than replaces, so values are collected as lists.
    """
    out: dict[str, list[str]] = {}
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section != "Service" or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out.setdefault(key.strip(), []).append(value.strip())
    return out


def _writable_paths(directives: dict[str, list[str]]) -> list[str]:
    paths: list[str] = []
    for value in directives.get("ReadWritePaths", []):
        for entry in shlex.split(value):
            # A leading '-' marks a path systemd tolerates being absent.
            paths.append(entry.lstrip("-+!"))
    for value in directives.get("StateDirectory", []):
        for entry in shlex.split(value):
            paths.append(f"/var/lib/{entry}")
    return paths


def _exists(path: Path) -> bool | None:
    """True, False, or None when we are not allowed to look.

    Path.exists() raises rather than returning False when a parent directory
    is unreadable, which is routine when checking a save under another user's
    home. That must not crash the check.
    """
    try:
        return path.exists()
    except OSError:
        return None


def _covered(target: Path, granted: list[str]) -> bool:
    try:
        target = target.resolve()
    except OSError:
        target = Path(os.path.abspath(target))
    for raw in granted:
        allowed = Path(raw)
        if not allowed.is_absolute():
            continue
        try:
            allowed = allowed.resolve()
        except OSError:
            allowed = Path(os.path.abspath(allowed))
        if target == allowed or allowed in target.parents:
            return True
    return False


def check(config: Config, unit_path: Path = DEFAULT_UNIT) -> list[Finding]:
    findings: list[Finding] = []
    try:
        text = unit_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [Finding("error", f"{unit_path} does not exist; the unit is not installed")]
    except OSError as exc:
        return [Finding("error", f"cannot read {unit_path}: {exc}")]

    directives = parse_unit(text)

    if PLACEHOLDER in text:
        findings.append(
            Finding(
                "error",
                f"{unit_path} still contains the placeholder {PLACEHOLDER}. "
                "Replace it with the SaveGames directory holding your world.",
            )
        )

    granted = _writable_paths(directives)
    hardened = bool(
        directives.get("ProtectSystem") or directives.get("ProtectHome")
    )

    # Every ReadWritePaths entry must exist before the service starts.
    for raw in directives.get("ReadWritePaths", []):
        for entry in shlex.split(raw):
            optional = entry.startswith("-")
            path = Path(entry.lstrip("-+!"))
            present = _exists(path)
            if present is None:
                findings.append(
                    Finding(
                        "warning",
                        f"cannot tell whether ReadWritePaths entry {path} exists; "
                        "a parent directory is not readable by this user. Re-run "
                        "as root to check it.",
                    )
                )
            elif not present and not optional:
                findings.append(
                    Finding(
                        "error",
                        f"ReadWritePaths lists {path}, which does not exist. "
                        "systemd builds the mount namespace before the service "
                        "runs, so this fails the unit with 226/NAMESPACE.",
                    )
                )

    if hardened:
        # The world directory, not just its parent: that is what gets written.
        needed = {
            "the save directory": config.palworld.save_dir,
            "the database directory": config.database.path.parent,
            "the backup directory": config.maintenance.backup_dir,
        }
        if config.maintenance.lock_file is not None:
            needed["the lock directory"] = config.maintenance.lock_file.parent
        for label, path in needed.items():
            if not _covered(path, granted):
                findings.append(
                    Finding(
                        "error",
                        f"{label} {path} is not covered by ReadWritePaths or "
                        "StateDirectory, so the service will not be able to "
                        "write it under ProtectSystem/ProtectHome.",
                    )
                )

    # The binary the unit will actually run.
    exec_start = directives.get("ExecStart", [])
    if exec_start:
        parts = shlex.split(exec_start[0].lstrip("-@+!:"))
        if parts:
            binary = Path(parts[0])
            if _exists(binary) is False:
                findings.append(
                    Finding("error", f"ExecStart runs {binary}, which does not exist")
                )
            elif not os.access(binary, os.X_OK):
                findings.append(
                    Finding("error", f"ExecStart runs {binary}, which is not executable")
                )
        # The unit should serve the same config this check was run against.
        if "-c" in parts or "--config" in parts:
            flag = "-c" if "-c" in parts else "--config"
            unit_config = Path(parts[parts.index(flag) + 1])
            if config.source_path and unit_config.resolve() != config.source_path.resolve():
                findings.append(
                    Finding(
                        "warning",
                        f"the unit serves {unit_config} but this check ran "
                        f"against {config.source_path}",
                    )
                )

    # The user the unit runs as has to reach the save directory.
    users = directives.get("User", [])
    if users:
        findings.extend(_check_user(users[-1], directives, config))

    return findings


def _first_untraversable(
    target: Path, uid: int, groups: set[int]
) -> tuple[Path, int] | None:
    """The highest ancestor the given user cannot cd into, if any.

    Reaching a file needs the execute bit on every directory along the way,
    which is a separate question from the permissions on the file itself and
    the one most easily missed.
    """
    try:
        resolved = target.resolve()
    except OSError:
        resolved = Path(os.path.abspath(target))
    for path in list(reversed(resolved.parents)) + [resolved]:
        try:
            info = path.stat()
        except OSError:
            # Cannot stat it, which usually means the blocker is further up and
            # has already been reported.
            return None
        mode = info.st_mode & 0o777
        if uid == 0:
            continue
        if info.st_uid == uid:
            allowed = bool(mode & 0o100)
        elif info.st_gid in groups:
            allowed = bool(mode & 0o010)
        else:
            allowed = bool(mode & 0o001)
        if not allowed:
            return (path, mode)
    return None


def _check_user(user: str, directives: dict[str, list[str]], config: Config) -> list[Finding]:
    import grp
    import pwd

    findings: list[Finding] = []
    try:
        entry = pwd.getpwnam(user)
    except KeyError:
        return [
            Finding(
                "error",
                f"the unit runs as user {user!r}, which does not exist. "
                f"Create it: useradd --system --home /opt/paleditor "
                f"--shell /usr/sbin/nologin {user}",
            )
        ]

    groups = {entry.pw_gid}
    for group in grp.getgrall():
        if user in group.gr_mem:
            groups.add(group.gr_gid)
    for value in directives.get("SupplementaryGroups", []):
        for name in shlex.split(value):
            try:
                groups.add(grp.getgrnam(name).gr_gid)
            except KeyError:
                findings.append(
                    Finding("error", f"SupplementaryGroups names {name!r}, which does not exist")
                )

    # Every ancestor needs the execute bit before the save itself matters.
    # A home directory at 0700 stops the walk dead, and no amount of group
    # membership or permissions further down makes any difference.
    blocker = _first_untraversable(config.palworld.save_dir, entry.pw_uid, groups)
    if blocker is not None:
        path, mode = blocker
        findings.append(
            Finding(
                "error",
                f"user {user!r} cannot traverse {path} (mode {mode:04o}), so it "
                f"cannot reach {config.palworld.save_dir} no matter what the "
                "permissions below it are. Either grant the group the execute "
                f"bit (chmod g+x {path}) or run the unit as the user that owns "
                "the save.",
            )
        )
        return findings

    save_dir = config.palworld.save_dir
    try:
        info = save_dir.stat()
    except OSError:
        return findings  # the config loader already reported this

    readable = bool(info.st_mode & 0o004) or (
        info.st_gid in groups and info.st_mode & 0o040
    ) or info.st_uid == entry.pw_uid
    writable = bool(info.st_mode & 0o002) or (
        info.st_gid in groups and info.st_mode & 0o020
    ) or info.st_uid == entry.pw_uid

    # Whoever writes Level.sav owns the replacement. If that is not the user
    # the game server runs as, the game cannot write its own save after the
    # first maintenance window, and paleditor can only put the ownership back
    # when it runs as root or already owns the file.
    if info.st_uid != entry.pw_uid:
        import pwd as _pwd

        try:
            owner = _pwd.getpwuid(info.st_uid).pw_name
        except KeyError:
            owner = str(info.st_uid)
        findings.append(
            Finding(
                "warning",
                f"the save is owned by {owner!r} but the unit runs as {user!r}. "
                "After a maintenance window the new Level.sav would belong to "
                f"{user!r}, and the game server may no longer be able to write "
                f"it. Running the unit as {owner!r} avoids this entirely.",
            )
        )

    if not readable:
        findings.append(
            Finding("error", f"user {user!r} cannot read {save_dir} (mode {info.st_mode & 0o777:04o})")
        )
    elif not writable:
        findings.append(
            Finding(
                "warning",
                f"user {user!r} can read but not write {save_dir} "
                f"(mode {info.st_mode & 0o777:04o}). Reads will work; the "
                "maintenance window will not.",
            )
        )
    return findings
