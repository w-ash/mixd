"""Health check endpoint for monitoring and readiness probes.

**Liveness is deliberately DB-free.** Two independent probes hit this endpoint
every 30s (Fly's ``[[http_service.checks]]`` and the Dockerfile ``HEALTHCHECK``),
so a ``SELECT 1`` here meant a query against Neon roughly every 15s, forever.
Neon's scale-to-zero suspends a compute after 5 minutes with *no query activity*
— on the Launch plan that timeout is fixed — so the probe alone guaranteed the
compute never suspended and billed 24/7.

Liveness ("is this process serving?") and DB reachability ("can it reach
Postgres?") are different questions; conflating them is what made the probe
expensive. The default answers the first. Pass ``?deep=true`` to also answer the
second — for manual diagnostics, not for an automated probe on a timer.

``?busy=true`` answers a third question — "is an import in flight?" — for the
pre-deploy gate in ``.github/workflows/release.yml`` (every Fly deploy strategy
replaces machines and would kill a run mid-import). It costs one count query,
so like ``deep`` it is for on-demand callers, never a probe on a timer. This
endpoint is auth-exempt (``_EXEMPT_API_PATHS``), which is precisely what lets
CI call it: the response is a boolean and two counts, no user data.

**Do not point Fly's check at the deep form.** Beyond the cost, a DB-dependent
*liveness* probe is actively harmful: Postgres is external, so during a Neon
outage every machine fails its check at once and Fly restarts them in a loop
that cannot fix anything. The trade this leaves is real and accepted — nothing
now detects "process up, database unreachable" automatically; that surfaces as
failing requests instead.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from src import __version__
from src.config import get_logger
from src.infrastructure.persistence.database.db_connection import get_engine

logger = get_logger(__name__)
router = APIRouter(tags=["health"])


async def _probe_database() -> str | None:
    """Return ``None`` when the database is reachable, else a user-facing error."""
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        from src.interface._shared.error_classification import (
            classify_database_error,
        )

        info = classify_database_error(exc)
        logger.warning(
            "Health check: database unavailable",
            category=info.category,
            detail=info.detail,
        )
        return info.user_message
    return None


async def _probe_busy() -> dict[str, bool | int | str]:
    """In-flight-work snapshot for the pre-deploy gate.

    Three signals, because each covers a window the others cannot: the count
    query sees runs that started (``operation_runs`` rows), the in-process
    queue registry sees export files still waiting to start (no row yet), and
    the streaming counter sees a POST still writing upload bytes to disk (no
    queue registered yet either). Any one alone reads "idle" at the wrong
    moment. ``stale_running_operation_runs`` is reporting, not busyness: rows
    older than the reaper's 12h bound are excluded from ``busy`` as
    reaper-dead (see ``count_running_runs``), and release.yml warns when the
    gate passes with a nonzero stale count.
    """
    from src.application.runner import execute_use_case
    from src.application.services.operation_run_reaper import count_running_runs
    from src.interface.api.services.import_queue import (
        any_queue_undrained,
        uploads_streaming,
    )

    # The queue and streaming halves are in-process reads and cannot fail;
    # only the DB count needs the guard.
    queue_pending = any_queue_undrained()
    streaming = uploads_streaming()
    try:
        counts = await execute_use_case(count_running_runs)
    except Exception as exc:
        # Two distinct failure modes, two distinct answers. An UNREACHABLE
        # endpoint means the app is not serving, so it cannot be importing —
        # release.yml's ``|| true`` fails open on that, correctly. A SERVING
        # app whose count query errors proves nothing about in-flight work, so
        # a 500 here — which the workflow's ``-f`` also reads as "unreachable"
        # — would deploy over a live import. Fail closed instead: report busy,
        # let the gate retry on its 30s cadence, and let a persistent DB
        # outage surface at the 30-minute deadline with the gate's message.
        logger.warning(
            "Busy probe: run count query failed — reporting busy (fail-closed)",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return {
            "busy": True,
            "busy_probe_error": type(exc).__name__,
            "import_queue_pending": queue_pending,
            "uploads_streaming": streaming,
        }
    return {
        "busy": counts.live > 0 or queue_pending or streaming > 0,
        "running_operation_runs": counts.live,
        "stale_running_operation_runs": counts.stale,
        "import_queue_pending": queue_pending,
        "uploads_streaming": streaming,
    }


@router.get("/health")
async def health_check(deep: bool = False, busy: bool = False) -> JSONResponse:
    """Report process liveness; ``?deep=true`` probes the DB, ``?busy=true``
    reports in-flight work.

    The shallow form never touches Postgres, so it is safe to call on a short
    interval without defeating Neon's scale-to-zero. It returns 200 whenever the
    process is serving. The deep form adds ``database`` and returns 503 when the
    probe fails, mirroring the pre-v0.10.2 behaviour for manual checks. The busy
    form adds ``busy`` / ``running_operation_runs`` /
    ``stale_running_operation_runs`` / ``import_queue_pending`` /
    ``uploads_streaming`` and always returns 200 — busyness is data for the
    deploy gate, not degradation.
    """
    from src.config.settings import settings

    # NOT a chat-availability gate: since v0.9.0.1 the real gate is per-user
    # (GET /assistant/status resolves the user's BYO key or the server fallback).
    # This reports only whether the *server* fallback key is set — false on a
    # BYO-only deployment where chat still works fine per user.
    server_anthropic_key_configured = bool(
        settings.credentials.anthropic_api_key.get_secret_value()
    )

    content: dict[str, str | bool | int] = {
        "status": "ok",
        "version": __version__,
        "server_anthropic_key_configured": server_anthropic_key_configured,
    }

    if busy:
        content.update(await _probe_busy())

    if not deep:
        return JSONResponse(content=content, status_code=200)

    db_error_message = await _probe_database()
    db_ok = db_error_message is None
    content["status"] = "ok" if db_ok else "degraded"
    content["database"] = "connected" if db_ok else "unavailable"
    if db_error_message:
        content["database_error"] = db_error_message

    return JSONResponse(content=content, status_code=200 if db_ok else 503)
