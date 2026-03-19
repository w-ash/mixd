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

    content: dict[str, str] = {
        "status": "ok" if db_ok else "degraded",
        "version": __version__,
        "database": "connected" if db_ok else "unavailable",
    }
    if db_error_message:
        content["database_error"] = db_error_message

    return JSONResponse(content=content, status_code=200 if db_ok else 503)
