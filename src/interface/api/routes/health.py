"""Health check endpoint for monitoring and readiness probes."""

from fastapi import APIRouter

from src import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Basic health check returning service status and version."""
    return {"status": "ok", "version": __version__}
