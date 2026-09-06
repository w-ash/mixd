"""Connector precondition verdicts, free of any web-framework import.

``token_access`` is the single predicate behind both the trigger routes' 409
envelope and the sync-target list's ``available`` flag, so the two can never
disagree about whether a surface is runnable. It lives here rather than in
``deps`` so response schemas can name the verdict types without importing
FastAPI or the token-storage seam.
"""

from collections.abc import Collection
from typing import Literal

from attrs import define

from src.domain.services.oauth_grant import missing_from_grant
from src.infrastructure.connectors._shared.token_storage import StoredToken

type ConnectorBlockedReason = Literal[
    "CONNECTOR_NOT_CONNECTED", "CONNECTOR_SCOPE_MISSING"
]
"""Why a connector-backed surface cannot run — the 409 envelope codes verbatim."""


@define(frozen=True, slots=True)
class ConnectorAccess:
    """Whether a user's stored credential satisfies one surface's preconditions."""

    blocked_reason: ConnectorBlockedReason | None = None
    missing_scopes: frozenset[str] = frozenset()

    @property
    def available(self) -> bool:
        return self.blocked_reason is None


def token_access(
    token: StoredToken | None, required_scopes: Collection[str]
) -> ConnectorAccess:
    """Judge one stored token against the scopes a surface needs.

    Token presence is the whole connection test — refresh and validity are the
    connector routes' job. A grant string that omits a required scope blocks
    separately from an absent token: ``CONNECTOR_SCOPE_MISSING`` deliberately
    leaves the connector *connected* (its other surfaces still work), so the two
    conditions need different remedies and therefore different codes.
    """
    if token is None:
        return ConnectorAccess("CONNECTOR_NOT_CONNECTED")
    if missing := missing_from_grant(token.get("scope"), required_scopes):
        return ConnectorAccess("CONNECTOR_SCOPE_MISSING", missing)
    return ConnectorAccess()
