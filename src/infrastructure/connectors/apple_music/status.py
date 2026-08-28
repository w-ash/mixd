"""Apple Music connector status probe."""

from src.domain.entities.connector import ConnectorStatus
from src.infrastructure.connectors._shared.connector_status import stored_token_status
from src.infrastructure.connectors._shared.token_storage import TokenStorage


async def get_apple_music_status(
    user_id: str,
    storage: TokenStorage | None = None,
) -> ConnectorStatus:
    """Apple Music status from the stored Music User Token — no network calls.

    A MUT cannot be validated without spending a ``/v1/me`` request and cannot
    be refreshed: expiry (fixed ~6-month lifetime) or a recorded
    ``reauth_required`` marker (written by the API client on a 403 rejection)
    both mean the user must re-run the MusicKit browser authorization —
    the ``marker_or_expired`` rule. Mirrors Spotify's convention for expected
    credential aging: the grant stays ``connected=True`` with
    ``auth_error="reauth_required"`` so the UI derives ``needs_reauth``
    ("one click to fix"), never ``expired``. Apple exposes no profile
    endpoint, so no ``account_name`` is ever stored — the probe reports None.
    """
    return await stored_token_status(
        "apple_music",
        "browser_bridge",
        user_id,
        storage,
        reauth_rule="marker_or_expired",
    )
