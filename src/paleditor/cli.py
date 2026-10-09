"""The paleditor command line.

Beyond serving, the interesting commands are the Phase 1 ones: verify-save and
dump-chest exist so the save-format assumptions in saves/fieldpaths.py can be
confirmed against the real world before any UI is trusted with them.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
from pathlib import Path

from . import __version__

DEFAULT_CONFIG = Path("/etc/paleditor/paleditor.toml")


def _package_dir() -> Path:
    """Where the running paleditor package was imported from."""
    import paleditor

    return Path(paleditor.__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    try:
        return args.handler(args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        from .errors import PaleditorError

        if isinstance(exc, PaleditorError):
            print(f"error: {exc}", file=sys.stderr)
            return 1
        raise


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paleditor",
        description="Browse and queue edits against a Palworld server save.",
    )
    # The install path is in here because a copied virtualenv is not
    # relocatable: its launcher shebang and its editable-install path file both
    # hold absolute paths, so a project copied to a new directory keeps running
    # the original source tree. Printing where the code came from turns a
    # baffling "my fix did nothing" into an obvious one.
    parser.add_argument(
        "--version",
        action="version",
        version=f"paleditor {__version__} (from {_package_dir()})",
    )
    # Accepted before the subcommand as well as after it, because both orders
    # are natural to type and argparse only supports one by default.
    parser.add_argument(
        "-c", "--config", type=Path, default=None, dest="global_config",
        help=f"config file (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--log-level", default="info",
        choices=["debug", "info", "warning", "error"],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def with_config(sp):
        sp.add_argument(
            "-c", "--config", type=Path, default=None,
            help=f"config file (default: {DEFAULT_CONFIG})",
        )
        return sp

    p = with_config(sub.add_parser("serve", help="run the web app"))
    p.add_argument("--no-scheduler", action="store_true",
                   help="do not start the scheduled maintenance window")
    p.set_defaults(handler=_serve)

    p = with_config(sub.add_parser("ingest", help="parse the save into the database"))
    p.set_defaults(handler=_ingest)

    p = with_config(sub.add_parser(
        "run-window",
        help="run the maintenance window now (stops the game server)",
    ))
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p.set_defaults(handler=_run_window)

    p = sub.add_parser("hash-password", help="generate an argon2id hash for the config")
    p.add_argument("--stdin", action="store_true", help="read the password from stdin")
    p.set_defaults(handler=_hash_password)

    p = with_config(sub.add_parser("check-config", help="validate a config file"))
    p.add_argument("--skip-paths", action="store_true",
                   help="do not check that save_dir and backup_dir exist")
    p.set_defaults(handler=_check_config)

    p = with_config(sub.add_parser(
        "verify-save",
        help="check the save-format assumptions against a real save",
    ))
    p.add_argument("--save-dir", type=Path, default=None,
                   help="defaults to [palworld] save_dir from the config")
    p.add_argument("--backend", default="palworld", choices=["palworld", "fixture"])
    p.add_argument("--oodle-library", type=Path, default=None)
    p.set_defaults(handler=_verify_save)

    p = with_config(sub.add_parser(
        "dump-chest",
        help="print one chest's raw slots, to read item ids back",
    ))
    p.add_argument("--save-dir", type=Path, default=None,
                   help="defaults to [palworld] save_dir from the config")
    p.add_argument("--backend", default="palworld", choices=["palworld", "fixture"])
    p.add_argument("--oodle-library", type=Path, default=None)
    p.add_argument("container_guid", nargs="?",
                   help="omit to list every chest found")
    p.set_defaults(handler=_dump_chest)

    p = with_config(sub.add_parser(
        "check-write",
        help="produce a rewritten save, to test the container swap on a COPY",
    ))
    p.add_argument("--save-dir", type=Path, default=None,
                   help="defaults to [palworld] save_dir from the config")
    p.add_argument("--out", type=Path, default=None,
                   help="where to write the rewritten Level.sav "
                        "(default: alongside the backup directory)")
    p.add_argument("--oodle-library", type=Path, default=None)
    p.set_defaults(handler=_check_write)

    p = with_config(sub.add_parser(
        "check-service",
        help="check the installed systemd unit against this config",
    ))
    p.add_argument("--unit", type=Path, default=None,
                   help="unit file (default: /etc/systemd/system/paleditor.service)")
    p.set_defaults(handler=_check_service)

    p = with_config(sub.add_parser(
        "fetch-icons",
        help="download item icons from the wiki (partial; see docs/icons.md)",
    ))
    p.add_argument("--all", action="store_true",
                   help="every catalogued item, not only those in the world")
    p.add_argument("--refresh", action="store_true",
                   help="re-download icons already present")
    p.set_defaults(handler=_fetch_icons)

    p = with_config(sub.add_parser(
        "import-item-names",
        help="read every item id and name from the game's own data files",
    ))
    p.add_argument("--pak", type=Path, default=None,
                   help="the game's .pak (default: found from save_dir)")
    p.add_argument("--language", default="en")
    p.set_defaults(handler=_import_item_names)

    p = with_config(sub.add_parser("init-db", help="create the database schema"))
    p.set_defaults(handler=_init_db)

    return parser


# -- handlers --------------------------------------------------------------


def _save_inputs(args):
    """Resolve save_dir and the Oodle library from flags, else the config.

    These commands are run during deployment, when the config already says
    where the save and the library are. Repeating them on the command line is
    how they get typed wrong.
    """
    save_dir = getattr(args, "save_dir", None)
    oodle = getattr(args, "oodle_library", None)
    config = None
    if save_dir is None or oodle is None:
        try:
            config = _load(args)
        except Exception as exc:
            if save_dir is None:
                raise SystemExit(
                    f"error: --save-dir was not given and the config could not "
                    f"be read: {exc}"
                ) from None
        if config is not None:
            save_dir = save_dir or config.palworld.save_dir
            oodle = oodle or config.palworld.oodle_library
    return save_dir, oodle, config


def _config_path(args) -> Path:
    return (
        getattr(args, "config", None)
        or getattr(args, "global_config", None)
        or DEFAULT_CONFIG
    )


def _load(args, *, check_paths: bool = True):
    from .config import load

    return load(_config_path(args), check_paths=check_paths)


def _serve(args) -> int:
    import socket

    import uvicorn

    from .app import create_app

    config = _load(args)
    app = create_app(config, start_scheduler=not args.no_scheduler)

    # uvicorn's host/port arguments take one address, but Server.run accepts a
    # list of already-bound sockets, so every configured listen entry is served
    # by the one process. That matters because the second entry is typically
    # the VPN address the friend group actually connects to.
    sockets = []
    for address in config.server.listen:
        try:
            sockets.append(_bind(address))
        except OSError as exc:
            for opened in sockets:
                opened.close()
            print(f"error: cannot bind {address}: {exc}", file=sys.stderr)
            return 1

    server = uvicorn.Server(uvicorn.Config(app, log_config=None))
    bound = ", ".join(str(a) for a in config.server.listen)
    print(f"listening on {bound}", file=sys.stderr)
    try:
        server.run(sockets=sockets)
    finally:
        for opened in sockets:
            opened.close()
    return 0


def _bind(address) -> "socket.socket":
    """Open a listening socket for one configured address."""
    import socket

    family = socket.AF_INET6 if ":" in address.host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if family == socket.AF_INET6:
        # One socket per configured address, so a v6 socket must not quietly
        # also claim the v4 wildcard.
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    sock.bind((address.host, address.port))
    sock.listen(2048)
    sock.set_inheritable(True)
    return sock


def _ingest(args) -> int:
    from . import ingest

    config = _load(args)
    result = ingest.run(config)
    print(
        f"rev {result.rev}: {result.chest_count} chests across "
        f"{result.base_count} bases in {result.duration_ms}ms"
    )
    if not result.lock_codes_available:
        print("warning: no lock codes found; contents-only mode", file=sys.stderr)
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if result.orphaned:
        print(f"note: {result.orphaned} chest(s) no longer appear in the save")
    return 0


def _run_window(args) -> int:
    from .maintenance import run_window

    config = _load(args)
    if not args.yes:
        print(
            f"This stops {config.palworld.server_unit}, applies the queued "
            "edits and restarts it."
        )
        if input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
            print("aborted")
            return 1
    report = run_window(config, trigger="cli")
    for step in report.steps:
        print(f"  {step}")
    print(
        f"claimed={report.claimed} applied={report.applied} "
        f"failed={report.failed} restored={report.restored}"
    )
    if report.error:
        print(f"error: {report.error}", file=sys.stderr)
        return 1
    return 0


def _hash_password(args) -> int:
    from .auth import hash_password

    if args.stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Again: "):
            print("passwords did not match", file=sys.stderr)
            return 1
    if not password:
        print("password cannot be empty", file=sys.stderr)
        return 1
    print(hash_password(password))
    return 0


def _check_config(args) -> int:
    config = _load(args, check_paths=not args.skip_paths)
    print(f"{_config_path(args)}: ok")
    print(f"  code         : {_package_dir()}")
    print(f"  listen       : {', '.join(str(a) for a in config.server.listen)}")
    print(f"  allow_public : {config.server.allow_public}")
    print(f"  save_dir     : {config.palworld.save_dir}")
    print(f"  backend      : {config.palworld.save_backend}")
    print(f"  schedule     : {config.maintenance.schedule}")
    print(f"  database     : {config.database.path}")
    print(f"  backups      : {config.maintenance.backup_dir}")
    print(f"  window lock  : {config.maintenance.lock_file}")
    from .scheduler import CronSchedule
    from datetime import datetime

    nxt = CronSchedule(config.maintenance.schedule).next_after(datetime.now())
    print(f"  next window  : {nxt.isoformat(timespec='minutes')}")
    return 0


def _verify_save(args) -> int:
    """Phase 1. Nothing downstream should be built until this passes."""
    from .saves import get_backend
    from .saves.fieldpaths import status_report
    from .savesource import pick

    save_dir, oodle, _ = _save_inputs(args)
    backend = (
        get_backend("palworld", oodle_library=oodle)
        if args.backend == "palworld" else get_backend(args.backend)
    )
    if not backend.available():
        print(
            f"the {args.backend} backend is not installed; "
            "pip install -e '.[parser]'",
            file=sys.stderr,
        )
        return 1

    source = pick(save_dir, prefer_backup=True)
    level = source.level_sav
    print(f"parsing {level} ({source.label}) with the {backend.name} backend…")
    snapshot = backend.read_snapshot(level)

    print(f"\nbases   : {len(snapshot.bases)}")
    print(f"chests  : {len(snapshot.chests)}")
    print(f"sha256  : {snapshot.save_sha256}")

    locked = [c for c in snapshot.chests if c.lock_code is not None]
    print(f"\nlock codes: {'FOUND' if snapshot.lock_codes_available else 'NOT FOUND'}")
    print(f"  chests carrying a code: {len(locked)}")
    if locked:
        sample = locked[0]
        print(f"  sample: {sample.container_guid} -> {sample.lock_code!r}")
        print("  Confirm this against a chest whose code you know.")
    else:
        print("  The app will run in contents-only mode. If you expected codes,")
        print("  the field has moved; update LOCK_CODE_CANDIDATES in")
        print("  src/paleditor/saves/fieldpaths.py.")

    item_ids = sorted({s.item_id for c in snapshot.chests for s in c.slots if s.item_id})
    print(f"\ndistinct item ids in containers: {len(item_ids)}")
    for item_id in item_ids[:40]:
        print(f"  {item_id}")
    if len(item_ids) > 40:
        print(f"  … and {len(item_ids) - 40} more")

    unassigned = [c for c in snapshot.chests if c.base_guid is None]
    print(f"\nchests not near any base camp: {len(unassigned)}")

    print("\nfield path status:")
    for field, state in status_report().items():
        print(f"  {field:28} {state}")

    for warning in snapshot.warnings:
        print(f"\nwarning: {warning}")

    print(
        "\nStill to confirm by hand: that these container GUIDs are unchanged "
        "after a server restart and a world save cycle. Run this twice with a "
        "restart in between and diff the GUID lists."
    )
    return 0


def _dump_chest(args) -> int:
    """Phase 1. Read item ids back from a container instead of guessing them."""
    from .saves import get_backend
    from .savesource import pick

    save_dir, oodle, _ = _save_inputs(args)
    backend = (
        get_backend("palworld", oodle_library=oodle)
        if args.backend == "palworld" else get_backend(args.backend)
    )
    if not backend.available():
        print(f"the {args.backend} backend is not installed", file=sys.stderr)
        return 1
    snapshot = backend.read_snapshot(pick(save_dir, prefer_backup=True).level_sav)

    if not args.container_guid:
        for chest in snapshot.chests:
            filled = sum(1 for s in chest.slots if s.item_id)
            print(
                f"{chest.container_guid}  {chest.object_type or '?':20} "
                f"{filled}/{chest.slot_count} full  lock={chest.lock_code or '-'}"
            )
        return 0

    wanted = args.container_guid.lower()
    chest = next(
        (c for c in snapshot.chests if c.container_guid.lower() == wanted), None
    )
    if chest is None:
        print(f"no chest with container GUID {args.container_guid}", file=sys.stderr)
        return 1
    print(json.dumps(
        {
            "container_guid": chest.container_guid,
            "object_type": chest.object_type,
            "coordinates": [chest.x, chest.y, chest.z],
            "guild_id": chest.guild_id,
            "lock_code": chest.lock_code,
            "base_guid": chest.base_guid,
            "slots": [
                {"slot_index": s.slot_index, "item_id": s.item_id,
                 "stack_count": s.stack_count}
                for s in chest.slots
            ],
        },
        indent=2,
    ))
    print(
        "\nCopy any new item ids into src/paleditor/data/items.json with "
        'provenance "confirmed"; the seeder never overwrites those.',
        file=sys.stderr,
    )
    return 0


def _check_write(args) -> int:
    """Rewrite the save unchanged, so the container swap can be tested safely.

    This server's world uses the Oodle (PlM) container. No working Oodle
    compressor is available, so paleditor writes the zlib (PlZ) container
    instead. Whether Palworld loads a PlZ save in place of a PlM one is the one
    thing paleditor cannot determine on its own, and the whole write path
    depends on it. This command makes the file; loading it is your step.
    """
    from .saves import container, get_backend
    from .savesource import pick

    save_dir, oodle, config = _save_inputs(args)
    out_path = args.out
    if out_path is None:
        # Beside the backups by default: a directory the service can already
        # write, and not inside the save directory the game server owns.
        base = config.maintenance.backup_dir if config else Path(".")
        out_path = base / "check-write-Level.sav"
        out_path.parent.mkdir(parents=True, exist_ok=True)

    source = pick(save_dir, prefer_backup=True)
    print(f"reading {source.level_sav} ({source.label})")
    raw = source.level_sav.read_bytes()
    box = container.read(raw, path=source.level_sav, oodle_library=oodle)
    print(f"  container {box.magic.decode()} type 0x{box.save_type:02x}, "
          f"{len(box.gvas):,} bytes of GVAS")

    backend = get_backend("palworld", oodle_library=oodle)
    if not backend.available():
        print("the palworld backend is unavailable", file=sys.stderr)
        return 1

    # No edits: the output differs from the input only by its container, which
    # isolates the question being asked.
    report = backend.apply_edits(source.level_sav, out_path, [])
    assert not report.failed
    written = out_path.stat().st_size
    print(f"  wrote {out_path} ({written:,} bytes, was {len(raw):,})")

    rewritten = container.read(out_path.read_bytes(), path=out_path)
    identical = rewritten.gvas == box.gvas
    print(f"  re-read as {rewritten.magic.decode()}; "
          f"GVAS identical to the original: {identical}")
    if not identical:
        print(
            "\nThe round-trip changed the world data. Do NOT load this save.",
            file=sys.stderr,
        )
        return 1

    print(
        "\nThe payload survived the round-trip byte for byte, so only the\n"
        "container changed. Now the part paleditor cannot test:\n"
        f"\n  1. Stop the server and back up {source.level_sav.name}\n"
        f"  2. Copy {out_path} over it\n"
        "  3. Start the server and confirm the world loads with everything intact\n"
        "  4. Restore your backup afterwards\n"
        "\nDo this on a copy of the world, or at a time you are happy to restore.\n"
        "Until it passes, leave the write path disabled."
    )
    return 0


def _check_service(args) -> int:
    """Catch the unit/config mismatches that otherwise surface as 226/NAMESPACE."""
    from .servicecheck import DEFAULT_UNIT, check

    config = _load(args, check_paths=False)
    unit = args.unit or DEFAULT_UNIT
    findings = check(config, unit)

    print(f"{unit}")
    if not findings:
        print("  ok: the unit matches this config")
        return 0
    errors = [f for f in findings if f.level == "error"]
    for finding in findings:
        mark = "ERROR  " if finding.level == "error" else "warning"
        print(f"  {mark} {finding.message}")
    if errors:
        print(
            f"\n{len(errors)} problem(s) would stop the service from starting "
            "or from writing the save."
        )
        return 1
    return 0


def _fetch_icons(args) -> int:
    from . import db, icons

    config = _load(args, check_paths=False)
    destination = db.icon_dir(config.database.path)
    print(f"downloading into {destination}")
    with db.closing_connect(config.database.path) as conn:
        report = icons.fetch(
            conn, destination,
            only_in_world=not args.all, refresh=args.refresh,
        )

    attempted = report.downloaded + len(report.unresolved) + len(report.errors)
    print(f"  downloaded : {report.downloaded}")
    print(f"  already had: {report.skipped}")
    print(f"  no match   : {len(report.unresolved)}")
    if report.errors:
        print(f"  failed     : {len(report.errors)}")
        for item_id, reason in list(report.errors.items())[:5]:
            print(f"      {item_id}: {reason}")
    if attempted:
        print(f"\n{report.downloaded}/{attempted} resolved.")
    if report.unresolved:
        print(
            "\nThe wiki keys images on display names, so an item can only be "
            "found once it\nhas one. These are still raw ids:\n"
        )
        for item_id in report.unresolved[:12]:
            print(f"    {item_id}")
        if len(report.unresolved) > 12:
            print(f"    ... and {len(report.unresolved) - 12} more")
        print(
            "\nName them in src/paleditor/data/items.json and run this again. "
            "Until then\nthey render as their category colour, which every "
            "item has."
        )
    return 0


def _import_item_names(args) -> int:
    """Teach the catalogue every item the game knows, and its real name."""
    from . import catalog, db
    from .gamedata import find_pak, item_names

    config = _load(args, check_paths=False)
    pak = args.pak or config.palworld.pak_file or find_pak(config.palworld.save_dir)
    if pak is None or not Path(pak).is_file():
        print(
            "could not find the game's .pak. Pass --pak, or set "
            "[palworld] pak_file.",
            file=sys.stderr,
        )
        return 1

    print(f"reading {pak}")
    names = item_names(
        Path(pak),
        language=args.language,
        oodle_library=config.palworld.oodle_library,
    )
    db.init(config.database.path)
    with db.closing_connect(config.database.path) as conn:
        imported = catalog.import_game_names(conn, names)
        total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        here = conn.execute(
            "SELECT COUNT(*) FROM items WHERE in_world = 1"
        ).fetchone()[0]
    print(f"  imported {imported} item name(s)")
    print(f"  catalogue now holds {total}, of which {here} are in this world")
    return 0


def _init_db(args) -> int:
    from . import db

    config = _load(args, check_paths=False)
    db.init(config.database.path)
    print(f"schema ready at {config.database.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
