"""Last.fm connector status probe."""

from src.config import settings
from src.domain.entities.connector import ConnectorStatus
from src.infrastructure.connectors._shared.token_storage import (
    TokenStorage,
    get_token_storage,
)


async def get_lastfm_status(
    user_id: str,
    storage: TokenStorage | None = None,
) -> ConnectorStatus:
    """Check Last.fm auth by looking up stored session key.

    Connected = has stored session key AND has API key configured.
    Falls back to env var check if no session key is stored (password-based
    auth obtains the session key on first authenticated request).
    """
    storage = storage or get_token_storage()
    token_data = await storage.load_token("lastfm", user_id)

    has_api_key = bool(settings.credentials.lastfm_key)
    has_session = token_data is not None and bool(token_data.get("session_key"))
    has_password = bool(
        settings.credentials.lastfm_password
        and settings.credentials.lastfm_password.get_secret_value()
    )
    has_username = bool(settings.credentials.lastfm_username)

    # Connected if we have a stored session key, OR if we have credentials
    # to obtain one (api_key + username + password)
    connected = has_api_key and (has_session or (has_username and has_password))

    account_name = (
        (token_data.get("account_name") if token_data else None)
        or settings.credentials.lastfm_username
        or None
    )

    return ConnectorStatus(
        name="lastfm",
        auth_method="oauth",
        connected=connected,
        account_name=account_name,
    )
