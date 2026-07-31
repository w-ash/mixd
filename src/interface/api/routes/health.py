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


@router.get("/health")
async def health_check(deep: bool = False) -> JSONResponse:
    """Report process liveness; with ``?deep=true``, also probe the database.

    The shallow form never touches Postgres, so it is safe to call on a short
    interval without defeating Neon's scale-to-zero. It returns 200 whenever the
    process is serving. The deep form adds ``database`` and returns 503 when the
    probe fails, mirroring the pre-v0.10.2 behaviour for manual checks.
    """
    from src.config.settings import settings

    # NOT a chat-availability gate: since v0.9.0.1 the real gate is per-user
    # (GET /assistant/status resolves the user's BYO key or the server fallback).
    # This reports only whether the *server* fallback key is set — false on a
    # BYO-only deployment where chat still works fine per user.
    server_anthropic_key_configured = bool(
        settings.credentials.anthropic_api_key.get_secret_value()
    )

    content: dict[str, str | bool] = {
        "status": "ok",
        "version": __version__,
        "server_anthropic_key_configured": server_anthropic_key_configured,
    }

    if not deep:
        return JSONResponse(content=content, status_code=200)

    db_error_message = await _probe_database()
    db_ok = db_error_message is None
    content["status"] = "ok" if db_ok else "degraded"
    content["database"] = "connected" if db_ok else "unavailable"
    if db_error_message:
        content["database_error"] = db_error_message

    return JSONResponse(content=content, status_code=200 if db_ok else 503)
