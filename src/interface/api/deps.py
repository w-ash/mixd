"""FastAPI dependencies for request-scoped values.

Provides dependency functions for extracting user identity and other
request-scoped context from the ASGI scope.
"""

from starlette.requests import Request

from src.config.constants import BusinessLimits


def get_current_user_id(request: Request) -> str:
    """Extract the current user's ID from Neon Auth JWT claims.

    Reads the ``sub`` claim from ``scope["auth_user"]``, which is set by
    ``NeonAuthMiddleware`` when a valid JWT is present. Falls back to
    ``DEFAULT_USER_ID`` when auth is disabled (local dev) or claims are missing.

    Usage in route handlers (v0.6.1)::

        @router.get("/tracks")
        async def list_tracks(user_id: str = Depends(get_current_user_id)): ...
    """
    claims = request.scope.get("auth_user")
    if claims and (sub := claims.get("sub")):
        return sub
    return BusinessLimits.DEFAULT_USER_ID
