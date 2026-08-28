"""Tidal connector status probe."""

from src.domain.entities.connector import ConnectorStatus
from src.infrastructure.connectors._shared.connector_status import stored_token_status
from src.infrastructure.connectors._shared.token_storage import TokenStorage


async def get_tidal_status(
    user_id: str,
    storage: TokenStorage | None = None,
) -> ConnectorStatus:
    """Tidal status from the stored token pair — storage only, no network.

    The bearer auth refreshes on use, so the probe never spends a refresh
    POST (unlike Spotify's silent-refresh-on-probe — kept cheap on purpose):
    the ``expired_without_refresh`` rule. An expired access token with no
    refresh token to renew it means only the reconnect flow helps:
    ``connected=True`` + ``auth_error="reauth_required"`` derives to
    ``needs_reauth`` (one-click fix). No marker check: unlike Apple, no
    Tidal path records a ``reauth_required`` marker — a dead grant is
    compare-and-deleted on ``invalid_grant``, which reads as disconnected
    here. An expired access token *beside* a refresh token is routine — the
    next API call rotates it silently, so the probe reports the stored
    expiry as-is with no error.

    The ``detail`` suffix renders the ``favorites_count`` the snapshot
    cached in ``extra_data`` (the Discogs stored-count pattern) — a count of
    0 still renders ("0 favorites" is the zero-state, not an error); a token
    stored before any snapshot ran yields ``detail=None``.
    """
    return await stored_token_status(
        "tidal",
        "oauth",
        user_id,
        storage,
        reauth_rule="expired_without_refresh",
        detail_key="favorites_count",
        detail_noun="favorites",
    )
