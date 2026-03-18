"""Health check endpoint for monitoring and readiness probes."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from src import __version__
from src.infrastructure.persistence.database.db_connection import get_engine

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> JSONResponse:
    """Health check with database connectivity probe."""
    db_ok = True
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    return JSONResponse(
        content={
            "status": "ok" if db_ok else "degraded",
            "version": __version__,
            "database": "connected" if db_ok else "unavailable",
        },
        status_code=200 if db_ok else 503,
    )
