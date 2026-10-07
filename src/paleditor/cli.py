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
    parser.add_argument("--version", action="version", version=f"paleditor {__version__}")
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

    p = sub.add_parser(
        "verify-save",
        help="Phase 1: check the save-format assumptions against a real save",
    )
    p.add_argument("--save-dir", type=Path, required=True)
    p.add_argument("--backend", default="cheahjs", choices=["cheahjs", "fixture"])
    p.set_defaults(handler=_verify_save)

    p = sub.add_parser(
        "dump-chest",
        help="Phase 1: print one chest's raw slots, to read item ids back",
    )
    p.add_argument("--save-dir", type=Path, required=True)
    p.add_argument("--backend", default="cheahjs", choices=["cheahjs", "fixture"])
    p.add_argument("container_guid", nargs="?",
                   help="omit to list every chest found")
    p.set_defaults(handler=_dump_chest)

    p = with_config(sub.add_parser("init-db", help="create the database schema"))
    p.set_defaults(handler=_init_db)

    return parser


# -- handlers --------------------------------------------------------------


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
    import uvicorn

    from .app import create_app

    config = _load(args)
    app = create_app(config, start_scheduler=not args.no_scheduler)

    # uvicorn binds one address per run, so additional listen entries are
    # reported rather than silently ignored.
    first = config.server.listen[0]
    if len(config.server.listen) > 1:
        print(
            f"note: binding {first} only. uvicorn takes one address; run one "
            "unit per listen entry, or put a reverse proxy in front.",
            file=sys.stderr,
        )
    uvicorn.run(app, host=first.host, port=first.port, log_config=None)
    return 0


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
    print(f"  listen       : {', '.join(str(a) for a in config.server.listen)}")
    print(f"  allow_public : {config.server.allow_public}")
    print(f"  save_dir     : {config.palworld.save_dir}")
    print(f"  backend      : {config.palworld.save_backend}")
    print(f"  schedule     : {config.maintenance.schedule}")
    print(f"  database     : {config.database.path}")
    from .scheduler import CronSchedule
    from datetime import datetime

    nxt = CronSchedule(config.maintenance.schedule).next_after(datetime.now())
    print(f"  next window  : {nxt.isoformat(timespec='minutes')}")
    return 0


def _verify_save(args) -> int:
    """Phase 1. Nothing downstream should be built until this passes."""
    from .saves import get_backend
    from .saves.fieldpaths import status_report

    backend = get_backend(args.backend)
    if not backend.available():
        print(
            f"the {args.backend} backend is not installed; "
            "pip install -e '.[parser]'",
            file=sys.stderr,
        )
        return 1

    level = args.save_dir / "Level.sav"
    if not level.is_file():
        print(f"{level} does not exist", file=sys.stderr)
        return 1

    print(f"parsing {level} with the {backend.name} backend…")
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

    backend = get_backend(args.backend)
    if not backend.available():
        print(f"the {args.backend} backend is not installed", file=sys.stderr)
        return 1
    snapshot = backend.read_snapshot(args.save_dir / "Level.sav")

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


def _init_db(args) -> int:
    from . import db

    config = _load(args, check_paths=False)
    db.init(config.database.path)
    print(f"schema ready at {config.database.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
