"""Unit tests for connector status state derivation.

``derive_status_state`` is the pure rule mapping a probe snapshot to the
UI-facing state enum; the v0.10.1 addition is ``scope_missing`` →
``needs_reauth`` (a narrower-grant signal, distinct from a broken session).
"""

import time

from src.domain.entities.connector import (
    ConnectorAuthError,
    ConnectorAuthMethod,
    ConnectorStatus,
    derive_status_state,
)


def make_status(
    *,
    auth_method: ConnectorAuthMethod = "oauth",
    connected: bool = True,
    auth_error: ConnectorAuthError | None = None,
    token_expires_at: int | None = None,
) -> ConnectorStatus:
    return ConnectorStatus(
        name="spotify",
        auth_method=auth_method,
        connected=connected,
        auth_error=auth_error,
        token_expires_at=token_expires_at,
    )


class TestDeriveStatusState:
    def test_scope_missing_maps_to_needs_reauth(self) -> None:
        status = make_status(auth_error="scope_missing")
        assert derive_status_state(status) == "needs_reauth"

    def test_refresh_failed_maps_to_error(self) -> None:
        status = make_status(auth_error="refresh_failed", connected=False)
        assert derive_status_state(status) == "error"

    def test_scope_missing_wins_over_expired(self) -> None:
        # An expired token with a scope gap still renders as needs_reauth —
        # one reconnect fixes both, and "session expired" would hide the
        # permissions ask.
        status = make_status(
            auth_error="scope_missing",
            token_expires_at=int(time.time()) - 100,
        )
        assert derive_status_state(status) == "needs_reauth"

    def test_coming_soon_short_circuits_auth_error(self) -> None:
        status = make_status(
            auth_method="coming_soon", connected=False, auth_error="scope_missing"
        )
        assert derive_status_state(status) == "coming_soon"

    def test_connected_without_error_stays_connected(self) -> None:
        status = make_status(token_expires_at=int(time.time()) + 3600)
        assert derive_status_state(status) == "connected"

    def test_not_connected_is_disconnected(self) -> None:
        status = make_status(connected=False)
        assert derive_status_state(status) == "disconnected"
