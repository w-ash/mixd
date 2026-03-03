"""Health check endpoint for monitoring and readiness probes."""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Basic health check returning service status and version."""
    return {"status": "ok", "version": "0.3.0"}
