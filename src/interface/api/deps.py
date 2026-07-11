"""FastAPI dependencies for request-scoped values.

Provides dependency functions for extracting user identity and other
request-scoped context from the ASGI scope.
"""

from collections.abc import Awaitable, Callable
from typing import cast

from fastapi import Depends, Request

from src.application.chat.protocols import LLMClientProtocol
from src.config.constants import BusinessLimits
from src.domain.exceptions import ConnectorNotConnectedError
from src.infrastructure.connectors._shared.token_storage import get_token_storage
from src.interface.api.auth_gate import JWTClaims


def get_current_user_id(request: Request) -> str:
    """Extract the current user's ID from Neon Auth JWT claims.

    Reads the ``sub`` claim from ``scope["auth_user"]``, which is set by
    ``NeonAuthMiddleware`` when a valid JWT is present. Falls back to
    ``DEFAULT_USER_ID`` when auth is disabled (local dev) or claims are missing.

    Usage in route handlers (v0.6.2)::

        @router.get("/tracks")
        async def list_tracks(user_id: str = Depends(get_current_user_id)): ...
    """
    raw_claims = request.scope.get("auth_user")
    if isinstance(raw_claims, dict):
        claims = cast(JWTClaims, raw_claims)
        if sub := claims.get("sub"):
            return sub
    return BusinessLimits.DEFAULT_USER_ID


def require_connector_connected(service: str) -> Callable[..., Awaitable[None]]:
    """Build a pre-flight dependency that 409s when ``service`` has no stored token.

    Gates the import/sync routes so a token-less user gets an immediate, actionable
    ``CONNECTOR_NOT_CONNECTED`` instead of a background operation that starts and
    then fails. A token-presence check only — refresh/validity is the connector
    routes' job. Mirrors the connectors route's direct use of ``get_token_storage``
    (the v0.6.5 OAuth-sharing architecture).
    """

    async def _dep(user_id: str = Depends(get_current_user_id)) -> None:
        if await get_token_storage().load_token(service, user_id) is None:
            raise ConnectorNotConnectedError(service)

    return _dep


def get_llm_client() -> LLMClientProtocol:
    """Resolve the chat LLM client (the injection seam tests override).

    Raises ``ChatUnavailableError`` when ``ANTHROPIC_API_KEY`` is unset, which
    the exception handlers map to a 503 ``CHAT_UNAVAILABLE`` envelope.
    """
    from src.infrastructure.chat.anthropic_adapter import get_anthropic_adapter

    return get_anthropic_adapter()
