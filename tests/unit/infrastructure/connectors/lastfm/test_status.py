"""Unit tests for the Last.fm connector status probe.

``get_lastfm_status`` is the one probe with a credential fallback: a stored
session key connects, but so does an API key plus username and password,
because password-based auth obtains the session key on first use. No API key
means disconnected whatever else is present. Storage-only — never a network
call.
"""

from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

from pydantic import SecretStr
import pytest

from src.config import settings
from src.config.settings import CredentialsConfig
from src.domain.entities.connector import derive_status_state
from src.infrastructure.connectors._shared.token_storage import StoredToken
from src.infrastructure.connectors.lastfm.status import get_lastfm_status


def make_storage(token: StoredToken | None) -> AsyncMock:
    storage = AsyncMock()
    storage.load_token = AsyncMock(return_value=token)
    return storage


def _session_token(account_name: str | None = "wash") -> StoredToken:
    token = StoredToken(session_key="sk-123")
    if account_name is not None:
        token["account_name"] = account_name
    return token


@pytest.fixture
def credentials() -> Iterator[CredentialsConfig]:
    """Swap in a blank credentials group the test fills per case."""
    creds = CredentialsConfig()
    with patch.object(settings, "credentials", creds):
        yield creds


class TestGetLastfmStatus:
    async def test_session_key_with_api_key_connects(
        self, credentials: CredentialsConfig
    ) -> None:
        credentials.lastfm_key = "api-key"
        storage = make_storage(_session_token())

        status = await get_lastfm_status("u1", storage)

        assert status.name == "lastfm"
        assert status.auth_method == "oauth"
        assert status.connected is True
        assert status.account_name == "wash"
        assert derive_status_state(status) == "connected"
        storage.load_token.assert_awaited_once_with("lastfm", "u1")

    async def test_no_api_key_disconnects_even_with_a_session(
        self, credentials: CredentialsConfig
    ) -> None:
        status = await get_lastfm_status("u1", make_storage(_session_token()))

        assert status.connected is False
        assert derive_status_state(status) == "disconnected"

    async def test_password_credentials_connect_without_a_session(
        self, credentials: CredentialsConfig
    ) -> None:
        # Password auth obtains the session key on first authenticated call,
        # so api key + username + password is a usable connection today.
        credentials.lastfm_key = "api-key"
        credentials.lastfm_username = "wash"
        credentials.lastfm_password = SecretStr("hunter2")

        status = await get_lastfm_status("u1", make_storage(None))

        assert status.connected is True
        assert status.account_name == "wash"

    async def test_username_without_password_disconnects(
        self, credentials: CredentialsConfig
    ) -> None:
        credentials.lastfm_key = "api-key"
        credentials.lastfm_username = "wash"

        status = await get_lastfm_status("u1", make_storage(None))

        assert status.connected is False
        assert status.account_name == "wash"

    async def test_token_account_name_wins_over_configured_username(
        self, credentials: CredentialsConfig
    ) -> None:
        credentials.lastfm_key = "api-key"
        credentials.lastfm_username = "env-user"

        status = await get_lastfm_status(
            "u1", make_storage(_session_token("token-user"))
        )

        assert status.account_name == "token-user"

    async def test_no_identity_anywhere_yields_none(
        self, credentials: CredentialsConfig
    ) -> None:
        status = await get_lastfm_status("u1", make_storage(None))

        assert status.connected is False
        assert status.account_name is None
