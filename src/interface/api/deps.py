"""FastAPI dependencies for request-scoped values.

Provides dependency functions for extracting user identity and other
request-scoped context from the ASGI scope.
"""

from collections.abc import Awaitable, Callable, Collection, Mapping
from typing import Literal, cast

from fastapi import Depends, Request

from src.application.chat.protocols import LLMClientProtocol
from src.application.use_cases._shared.sync_targets import SYNC_TARGETS, SyncTarget
from src.config.constants import BusinessLimits
from src.domain.exceptions import (
    ChatUnavailableError,
    ConnectorNotConnectedError,
    ConnectorScopeMissingError,
)
from src.infrastructure.connectors._shared.token_storage import get_token_storage
from src.interface.api.auth_gate import JWTClaims
from src.interface.api.connector_access import ConnectorAccess, token_access


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


async def check_connector_access(
    service: str, user_id: str, required_scopes: Collection[str] = ()
) -> ConnectorAccess:
    """Load ``service``'s stored token for ``user_id`` and judge it."""
    token = await get_token_storage().load_token(service, user_id)
    return token_access(token, required_scopes)


def _raise_if_blocked(service: str, access: ConnectorAccess) -> None:
    """Turn a blocked verdict into the 409 the frontend's connect prompt reads."""
    if access.blocked_reason == "CONNECTOR_NOT_CONNECTED":
        raise ConnectorNotConnectedError(service)
    if access.blocked_reason == "CONNECTOR_SCOPE_MISSING":
        raise ConnectorScopeMissingError(service, access.missing_scopes)


def require_sync_target_access(target_id: SyncTarget) -> Callable[..., Awaitable[None]]:
    """Build a pre-flight dependency that 409s unless ``target_id`` can run.

    Its connector and scope demands come from ``SYNC_TARGETS[target_id]``, the
    same spec ``sync_target_access`` judges the list endpoint's availability
    flag from, so a target can never advertise itself as runnable and then be
    refused at its own trigger route. A token-less (or under-scoped) user gets
    an immediate, actionable error instead of a background operation that starts
    and then fails inside the importer.
    """
    spec = SYNC_TARGETS[target_id]

    async def _dep(user_id: str = Depends(get_current_user_id)) -> None:
        access = await check_connector_access(
            spec.service, user_id, spec.required_scopes
        )
        _raise_if_blocked(spec.service, access)

    return _dep


async def sync_target_access(
    user_id: str = Depends(get_current_user_id),
) -> Mapping[str, ConnectorAccess]:
    """Verdict per sync target for this user, in one token read.

    Two targets can share a connector with different scope demands, so every
    service the registry names is read in one query and the pure predicate is
    applied per target. One batched read, not one per connector: this runs on
    every Sync page load, and a session per service would size the pool by the
    connector count.
    """
    services = sorted({spec.service for spec in SYNC_TARGETS.values()})
    tokens = await get_token_storage().load_tokens(services, user_id)
    return {
        target: token_access(tokens.get(spec.service), spec.required_scopes)
        for target, spec in SYNC_TARGETS.items()
    }


async def trigger_play_refresh(user_id: str = Depends(get_current_user_id)) -> None:
    """Kick off a play refresh for routes that read play data, without waiting.

    Attached via ``dependencies=[...]`` so handler signatures stay untouched.
    There is no single REST chokepoint for "reads plays" — the dashboard and the
    track detail are separate routes — so the dependency travels to each rather
    than the routes reaching for a service.

    Fire-and-forget: the response must never wait on a third-party API. The
    refresh lands for the *next* read, which is the right trade for a surface
    someone is actively browsing. Staleness gating and single-flight are the poll
    policy's job, so calling this on every request is cheap and idempotent —
    FastAPI also caches a dependency's result within one request, so attaching it
    twice costs nothing.
    """
    from src.application.services.play_freshness import spawn_ensure_fresh_plays

    spawn_ensure_fresh_plays(user_id, trigger_detail="web")


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
