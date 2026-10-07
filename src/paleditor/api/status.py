"""Status, and the owner-only trigger for the maintenance window."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from .. import auth, queries
from ..config import Config
from ..errors import WindowBusy
from ..locking import FileLock
from ..models import StatusResponse
from ..saves import get_backend
from ..scheduler import CronSchedule
from ..servercontrol import SystemdServerControl
from .deps import get_config, get_conn, get_session, require_owner

log = logging.getLogger(__name__)
router = APIRouter(tags=["status"])

STALE_AFTER_HOURS = 26


@router.get("/status", response_model=StatusResponse)
def status_(
    config: Config = Depends(get_config),
    conn: sqlite3.Connection = Depends(get_conn),
    _=Depends(get_session),
) -> StatusResponse:
    last = queries.last_ingest(conn)
    last_good = queries.last_good_ingest(conn)
    warnings: list[str] = []

    stale = False
    if last_good is None:
        stale = True
        warnings.append("no successful ingest yet; there is nothing to show")
    elif last_good.get("finished_at"):
        try:
            age = datetime.now().astimezone() - datetime.fromisoformat(
                last_good["finished_at"]
            )
            stale = age.total_seconds() > STALE_AFTER_HOURS * 3600
            if stale:
                warnings.append(
                    f"the newest data is {int(age.total_seconds() // 3600)}h old"
                )
        except ValueError:
            pass

    if last and last["status"] == "failed":
        # Showing stale data with a warning beats guessing: a failed ingest
        # usually means a game update moved the save layout.
        warnings.append(
            f"the last ingest failed: {last.get('error') or 'unknown error'}"
        )

    if not queries.lock_codes_available(conn):
        warnings.append(
            "no lock codes were found in the save; running in contents-only mode"
        )

    try:
        from .. import ingest as _ingest
        backend_available = _ingest._backend_for(config).available()
    except Exception:
        backend_available = False
    if not backend_available:
        warnings.append(
            f"the {config.palworld.save_backend} save backend is not installed, "
            "so ingest and the write path cannot run"
        )

    server_running: bool | None
    try:
        server_running = SystemdServerControl(config.palworld.server_unit).is_running()
    except Exception:
        server_running = None

    next_window = None
    if config.maintenance.enabled:
        try:
            next_window = CronSchedule(config.maintenance.schedule).next_after(
                datetime.now()
            ).isoformat(timespec="minutes")
        except Exception:
            pass

    return StatusResponse(
        server_running=server_running,
        last_ingest=last,
        last_good_ingest=last_good,
        next_window=next_window,
        queue=queries.queue_depth(conn),
        lock_codes_available=queries.lock_codes_available(conn),
        stale=stale,
        window_in_progress=FileLock(config.maintenance.lock_file).is_locked(),
        save_backend=config.palworld.save_backend,
        backend_available=backend_available,
        warnings=warnings,
    )


@router.post("/maintenance/run")
def run_maintenance(
    background: BackgroundTasks,
    confirm: bool = Query(
        default=False,
        description="Must be true. The window stops the game server.",
    ),
    config: Config = Depends(get_config),
    conn: sqlite3.Connection = Depends(get_conn),
    session: auth.Session = Depends(require_owner),
):
    """Run the window now. Owner only, and requires explicit confirmation.

    The run happens in the background: the sequence takes minutes, and holding
    an HTTP request open for it would just time out.
    """
    if not confirm:
        depth = queries.queue_depth(conn)
        raise HTTPException(
            status_code=400,
            detail=(
                "this stops the game server, applies "
                f"{depth['queued']} queued edit(s) and restarts it. "
                "Repeat with confirm=true to proceed."
            ),
        )
    if FileLock(config.maintenance.lock_file).is_locked():
        raise HTTPException(
            status_code=409,
            detail="a maintenance window is already running",
        )
    from .. import ingest as _ingest
    if not _ingest._backend_for(config).available():
        raise HTTPException(
            status_code=503,
            detail=(
                f"the {config.palworld.save_backend} save backend is not "
                "installed, so the write path cannot run"
            ),
        )
    last_good = queries.last_good_ingest(conn)
    if last_good is None:
        # The write path refuses entirely until ingest has passed once.
        raise HTTPException(
            status_code=503,
            detail=(
                "there has been no successful ingest, so paleditor does not "
                "know this save's layout well enough to write to it"
            ),
        )

    background.add_task(_run_window_safely, config)
    return {
        "started": True,
        "queued": queries.queue_depth(conn)["queued"],
        "detail": "the window is running; watch /api/status",
    }


def _run_window_safely(config: Config) -> None:
    from ..maintenance import run_window

    try:
        report = run_window(config, trigger="manual")
        log.info(
            "manual window finished: applied=%s failed=%s error=%s",
            report.applied, report.failed, report.error,
        )
    except WindowBusy as exc:
        log.warning("manual window refused: %s", exc)
    except Exception:
        log.exception("manual window crashed")


@router.post("/ingest/run")
def run_ingest_now(
    background: BackgroundTasks,
    config: Config = Depends(get_config),
    session: auth.Session = Depends(require_owner),
):
    """Reingest without touching the world. Read-only, but owner-gated anyway
    because a parse is expensive and can contend with the running server."""
    background.add_task(_run_ingest_safely, config)
    return {"started": True}


def _run_ingest_safely(config: Config) -> None:
    from .. import ingest

    try:
        result = ingest.run(config)
        log.info("manual ingest rev %s: %s chests", result.rev, result.chest_count)
    except Exception:
        log.exception("manual ingest failed")
