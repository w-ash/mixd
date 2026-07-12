"""FastAPI dependencies for request-scoped values.

Provides dependency functions for extracting user identity and other
request-scoped context from the ASGI scope.
"""

from collections.abc import Awaitable, Callable
from typing import Literal, cast

from fastapi import Depends, Request

from src.application.chat.protocols import LLMClientProtocol
from src.config.constants import BusinessLimits
from src.domain.exceptions import ChatUnavailableError, ConnectorNotConnectedError
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


async def get_llm_client(user_id: str) -> LLMClientProtocol:
    """Resolve the acting user's chat LLM client (the injection seam tests override).

    Precedence (user key → server fallback → none) lives in one place —
    ``resolve_chat_credential``. When it yields nothing, raises
    ``ChatUnavailableError`` → 503 ``CHAT_UNAVAILABLE``. One user's key never
    resolves for another (RLS-scoped storage keyed by ``user_id``).
    """
    from src.infrastructure.chat.anthropic_adapter import get_anthropic_adapter_for_key
    from src.infrastructure.chat.credentials import resolve_chat_credential

    resolved = await resolve_chat_credential(user_id)
    if resolved is None:
        raise ChatUnavailableError(
            "The chat assistant is not configured. Add your Anthropic API key in "
            "Settings > Assistant."
        )
    return get_anthropic_adapter_for_key(resolved[0])


async def resolve_chat_source(user_id: str) -> Literal["user", "server"] | None:
    """Report which credential (if any) would serve this user's chat turn.

    Drives the per-user ``GET /assistant/status`` capability signal the frontend
    gate consumes. ``None`` means no assistant is available for this user.
    """
    from src.infrastructure.chat.credentials import resolve_chat_credential

    resolved = await resolve_chat_credential(user_id)
    return resolved[1] if resolved else None
