"""Shared connector-status primitives.

Each connector owns its ``get_<service>_status`` probe in its own
``<service>/status.py`` — auth semantics diverge per service (Spotify's
silent OAuth refresh, Last.fm's api-key + session-key fallback, stubs that
skip auth entirely). This module keeps only what is genuinely shared: the
stored-token probe shape (:func:`stored_token_status`) and the registry
fan-out (:func:`get_all_connector_statuses`).
"""

import asyncio
import time
from typing import Literal

from src.domain.entities.connector import (
    ConnectorAuthError,
    ConnectorAuthMethod,
    ConnectorStatus,
)
from src.infrastructure.connectors._shared.token_storage import (
    StoredToken,
    TokenStorage,
    get_token_storage,
)

type ReauthRule = Literal["never", "marker_or_expired", "expired_without_refresh"]
"""When a stored token derives ``auth_error="reauth_required"``.

``never`` — the credential does not age (Discogs personal access token).
``marker_or_expired`` — a recorded ``extra_data["reauth_required"]`` marker
or a passed ``expires_at`` (Apple's Music User Token: unrefreshable, and the
API client stamps the marker on a 403 rejection).
``expired_without_refresh`` — a passed ``expires_at`` with no refresh token
to renew it (Tidal: an expired access token *beside* a refresh token is
routine — the bearer auth rotates it on next use — and no Tidal path records
a marker, so a stray one is ignored).
"""


def _needs_reauth(
    rule: ReauthRule,
    token_data: StoredToken,
    extra_data: dict[str, object],
    expired: bool,
) -> bool:
    """Apply ``rule`` to the stored token: does it need reauthorization?"""
    if rule == "marker_or_expired":
        return bool(extra_data.get("reauth_required")) or expired
    if rule == "expired_without_refresh":
        return expired and not token_data.get("refresh_token")
    return False


async def stored_token_status(
    service: str,
    auth_method: ConnectorAuthMethod,
    user_id: str,
    storage: TokenStorage | None,
    *,
    reauth_rule: ReauthRule = "never",
    detail_key: str | None = None,
    detail_noun: str = "",
) -> ConnectorStatus:
    """Status of a connector probed from its stored token alone — no network.

    Loads the token, applies ``reauth_rule``, and renders the cached integer
    count ``extra_data[detail_key]`` as ``"<count:,> <detail_noun>"`` — a
    count of 0 still renders (the zero-state invitation, not an error); a
    missing or non-integer count yields ``detail=None``. ``reauth_required``
    keeps ``connected=True`` so the UI derives ``needs_reauth`` (one-click
    fix), never ``expired``.
    """
    storage = storage or get_token_storage()
    token_data = await storage.load_token(service, user_id)

    if token_data is None:
        return ConnectorStatus(name=service, auth_method=auth_method, connected=False)

    expires_at = token_data.get("expires_at", 0) or 0
    extra_data = token_data.get("extra_data") or {}

    expired = expires_at <= time.time()
    auth_error: ConnectorAuthError | None = (
        "reauth_required"
        if _needs_reauth(reauth_rule, token_data, extra_data, expired)
        else None
    )

    detail: str | None = None
    if detail_key is not None:
        count = extra_data.get(detail_key)
        if isinstance(count, int):
            detail = f"{count:,} {detail_noun}"

    return ConnectorStatus(
        name=service,
        auth_method=auth_method,
        connected=True,
        account_name=token_data.get("account_name"),
        token_expires_at=int(expires_at) if expires_at else None,
        auth_error=auth_error,
        detail=detail,
    )


async def get_all_connector_statuses(user_id: str) -> list[ConnectorStatus]:
    """Probe every registered connector concurrently and return their statuses.

    Iterates the discovery registry so adding a connector only requires
    registering a new module — no edits here. Uses ``asyncio.TaskGroup`` for
    structured cancellation: a single status-probe failure surfaces cleanly
    instead of leaking orphaned tasks.
    """
    from src.infrastructure.connectors.discovery import discover_connectors

    registry = discover_connectors()
    storage = get_token_storage()

    async with asyncio.TaskGroup() as tg:
        tasks: dict[str, asyncio.Task[ConnectorStatus]] = {
            name: tg.create_task(config["status_fn"](user_id, storage))
            for name, config in registry.items()
        }

    return [tasks[name].result() for name in registry]
