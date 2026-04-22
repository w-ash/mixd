"""Connector domain vocabulary.

Defines *what a music service connector is* in domain terms: the categories
music services fall into, the ways users authenticate with them, the
capabilities any connector can declare, the runtime states a connection
can be in, and the pure rule that maps raw probe data to a UI-facing state.

Provider-specific knowledge (OAuth flow assembly, session-key exchange,
refresh semantics, client IDs) lives in ``src/infrastructure/connectors/``.
"""

import time
from typing import Literal

from attrs import define

type ConnectorCategory = Literal["streaming", "enrichment", "history"]
"""Taxonomy of music-service kinds, used to group connectors on the UI."""

type ConnectorAuthMethod = Literal["oauth", "none", "coming_soon"]
"""How a user connects: OAuth flow, no auth (public API), or not-yet-implemented."""

type ConnectorStatusState = Literal[
    "connected", "disconnected", "expired", "error", "public_api", "coming_soon"
]
"""UI-facing state derived from raw status + auth method."""

type ConnectorAuthError = Literal["refresh_failed"]
"""Server-observed auth failure codes. Widen as new failure modes arise."""

type Capability = Literal[
    "playlist_import",
    "playlist_sync",
    "likes_import",
    "history_import_file",
    "history_import_api",
    "track_enrichment",
    "love_tracks",
]
"""Stable capability names. Narrowed to a Literal union so a typo like
``"playlist_impot"`` is a compile error in connector configs, route handlers,
Pydantic serialisation, and — via Orval — in the frontend's
``Array.prototype.includes`` calls. Adding a capability touches only this
definition; existing consumers stay exhaustive."""


@define(frozen=True, slots=True)
class ConnectorStatus:
    """Immutable snapshot of a user's connection to one music service.

    Carries both the live probe result (``connected``, ``account_name``,
    ``token_expires_at``, ``auth_error``) and the static ``auth_method``
    that the registry declared, so downstream consumers have a complete
    snapshot without joining against the registry. Freshness data — last
    successful sync — is overlayed separately by the route handler.
    """

    name: str
    auth_method: ConnectorAuthMethod
    connected: bool
    account_name: str | None = None
    token_expires_at: int | None = None
    auth_error: ConnectorAuthError | None = None


def derive_status_state(status: ConnectorStatus) -> ConnectorStatusState:
    """Compute the UI-facing state enum from a connector status snapshot.

    Pure rule — no I/O, no provider knowledge. Ordering matters: non-OAuth
    connectors (``coming_soon``, ``none``) short-circuit before we consult
    probe data, so a misbehaving stub reporting an ``auth_error`` still
    renders as its static state.
    """
    if status.auth_method == "coming_soon":
        return "coming_soon"
    if status.auth_method == "none":
        return "public_api"
    if status.auth_error is not None:
        return "error"
    if not status.connected:
        return "disconnected"
    if status.token_expires_at and status.token_expires_at < time.time():
        return "expired"
    return "connected"
