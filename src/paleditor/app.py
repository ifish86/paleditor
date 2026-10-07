"""The FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .config import Config
from .errors import MaintenanceError, PaleditorError, SaveFormatError, WindowBusy
from .scheduler import IntervalWorker, WindowScheduler

log = logging.getLogger(__name__)

# Where the built Quasar SPA lands. Served by the app so the deployment is one
# systemd unit with no separate web server.
FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist" / "spa"


def create_app(config: Config, *, start_scheduler: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.init(config.database.path)
        _seed_items(config)
        ingest_worker = None
        if config.ingest.enabled:
            ingest_worker = IntervalWorker(
                config.ingest.interval_seconds,
                lambda: _periodic_ingest(config),
                name="ingest",
            )
            ingest_worker.start()
        app.state.ingest_worker = ingest_worker
        scheduler = None
        if start_scheduler and config.maintenance.enabled:
            scheduler = WindowScheduler(
                config.maintenance.schedule, lambda: _scheduled_window(config)
            )
            scheduler.start()
            app.state.scheduler = scheduler
        else:
            app.state.scheduler = None
            if not config.maintenance.enabled:
                log.warning(
                    "[maintenance] enabled is false: queued edits will only be "
                    "applied by a manual run"
                )
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.stop()
            if ingest_worker is not None:
                ingest_worker.stop()

    app = FastAPI(
        title="paleditor",
        version="0.1.0",
        description=(
            "Read-mostly API over a Palworld dedicated server save. Writes are "
            "queued and applied only during the maintenance window."
        ),
        lifespan=lifespan,
    )
    app.state.config = config

    from .api import chests, session, status

    app.include_router(session.router, prefix="/api")
    app.include_router(chests.router, prefix="/api")
    app.include_router(status.router, prefix="/api")

    _register_error_handlers(app)
    _mount_frontend(app)
    return app


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(WindowBusy)
    async def _busy(_request, exc: WindowBusy):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(SaveFormatError)
    async def _format(_request, exc: SaveFormatError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(MaintenanceError)
    async def _maintenance(_request, exc: MaintenanceError):
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    @app.exception_handler(PaleditorError)
    async def _generic(_request, exc: PaleditorError):
        return JSONResponse(status_code=500, content={"detail": str(exc)})


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built SPA, if it has been built.

    Absent, the API still works; only the browser UI is missing. That keeps the
    backend deployable before the frontend toolchain exists on the host.
    """
    if not FRONTEND_DIST.is_dir():
        log.info("no built frontend at %s; serving the API only", FRONTEND_DIST)

        @app.get("/", include_in_schema=False)
        async def _no_frontend():
            return JSONResponse(
                {
                    "detail": (
                        "the API is running but the frontend has not been built. "
                        "Run 'npm ci && npx quasar build' in frontend/."
                    ),
                    "docs": "/docs",
                }
            )

        return

    app.mount(
        "/assets",
        StaticFiles(directory=FRONTEND_DIST / "assets"),
        name="assets",
    )

    @app.get("/", include_in_schema=False)
    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str = ""):
        # Vue Router owns client-side routes, so anything that is not a real
        # file falls through to index.html.
        candidate = (FRONTEND_DIST / path).resolve()
        if path and candidate.is_file() and FRONTEND_DIST in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")


def _seed_items(config: Config) -> None:
    from . import catalog

    with db.closing_connect(config.database.path) as conn:
        count = catalog.seed(conn)
    log.info("seeded %s item(s) into the catalogue", count)


def _periodic_ingest(config: Config) -> None:
    """Reread the world on a timer.

    Without this the database only moves when somebody runs ingest by hand: the
    maintenance window reingests as its verification step, but it returns early
    when the queue is empty, which is most nights.
    """
    from . import ingest
    from .locking import FileLock

    if FileLock(config.maintenance.lock_file).is_locked():
        log.debug("maintenance window running; skipping this ingest")
        return
    try:
        result = ingest.run_if_changed(config)
    except Exception as exc:
        # Logged, not raised: a game update that breaks the parse must leave
        # the last good rev being served with a warning, not kill the worker.
        log.warning("periodic ingest failed: %s", exc)
        return
    if result is not None:
        log.info(
            "ingest rev %s: %s chests across %s bases in %sms",
            result.rev, result.chest_count, result.base_count, result.duration_ms,
        )


def _scheduled_window(config: Config) -> None:
    from .maintenance import run_window

    try:
        report = run_window(config, trigger="schedule")
        log.info(
            "scheduled window finished: claimed=%s applied=%s failed=%s",
            report.claimed, report.applied, report.failed,
        )
    except WindowBusy as exc:
        log.warning("scheduled window skipped: %s", exc)
