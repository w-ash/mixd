"""Health check endpoint for monitoring and readiness probes."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from src import __version__
from src.config import get_logger
from src.infrastructure.persistence.database.db_connection import get_engine

logger = get_logger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> JSONResponse:
    """Health check with database connectivity probe."""
    db_ok = True
    db_error_message: str | None = None
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        db_ok = False
        from src.infrastructure.persistence.database.error_classification import (
            classify_database_error,
        )

        info = classify_database_error(exc)
        db_error_message = info.user_message
        logger.warning(
            "Health check: database unavailable",
            category=info.category,
            detail=info.detail,
        )

    from src.config.settings import settings

    # NOT a chat-availability gate: since v0.9.0.1 the real gate is per-user
    # (GET /assistant/status resolves the user's BYO key or the server fallback).
    # This reports only whether the *server* fallback key is set — false on a
    # BYO-only deployment where chat still works fine per user.
    server_anthropic_key_configured = bool(
        settings.credentials.anthropic_api_key.get_secret_value()
    )

    content: dict[str, str | bool] = {
        "status": "ok" if db_ok else "degraded",
        "version": __version__,
        "database": "connected" if db_ok else "unavailable",
        "server_anthropic_key_configured": server_anthropic_key_configured,
    }
    if db_error_message:
        content["database_error"] = db_error_message

    return JSONResponse(content=content, status_code=200 if db_ok else 503)
