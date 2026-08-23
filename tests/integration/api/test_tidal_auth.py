"""Integration tests for the Tidal OAuth callback and connector registration.

Covers ``GET /auth/tidal/callback`` (state validation with the service
predicate, token persistence, redirect targets), the auth-url gate for the
registered Tidal connector, and the ``TidalAuthRequiredError`` /
``TidalReauthRequiredError`` → 409 middleware envelope. The token exchange
itself is patched — no Tidal network calls happen anywhere.
"""

from collections.abc import Generator
from unittest.mock import AsyncMock, patch
import urllib.parse

from fastapi import FastAPI
import httpx2
import pytest

from src.config.settings import CredentialsConfig, settings
from src.domain.exceptions import TidalReauthRequiredError
from src.infrastructure.connectors._shared.token_storage import StoredToken
from src.infrastructure.persistence.repositories.token_storage import (
    DatabaseTokenStorage,
)
from src.interface.api.middleware import register_exception_handlers

TEST_USER = "default"

_EXCHANGE = "src.interface.api.routes.auth.tidal_exchange_code"


def _exchanged_token() -> StoredToken:
    return StoredToken(
        access_token="tidal-at",
        refresh_token="tidal-rt",
        token_type="Bearer",
        expires_in=43200,
        expires_at=2_000_000_000,
        extra_data={"authorized_at": 1_900_000_000},
    )


@pytest.fixture
def tidal_creds() -> Generator[None]:
    creds = CredentialsConfig(
        tidal_client_id="tidal-client-id",
        tidal_redirect_uri="http://test/auth/tidal/callback",
    )
    with patch.object(settings, "credentials", creds):
        yield


@pytest.fixture
async def clean_tidal_token(client: httpx2.AsyncClient) -> None:
    """oauth_tokens is preserved across tests — start from nothing."""
    await DatabaseTokenStorage().delete_token("tidal", TEST_USER)


async def mint_state(client: httpx2.AsyncClient) -> str:
    """Create a real CSRF state row via the public auth-url endpoint."""
    resp = await client.get("/api/v1/connectors/tidal/auth-url")
    assert resp.status_code == 200, resp.text
    query = urllib.parse.urlparse(resp.json()["auth_url"]).query
    return urllib.parse.parse_qs(query)["state"][0]


async def load_tidal_token() -> StoredToken | None:
    return await DatabaseTokenStorage().load_token("tidal", TEST_USER)


class TestAuthUrl:
    """GET /api/v1/connectors/tidal/auth-url — oauth connector minting."""

    async def test_auth_url_points_at_tidal_login_with_pkce(
        self, client: httpx2.AsyncClient, tidal_creds: None
    ) -> None:
        resp = await client.get("/api/v1/connectors/tidal/auth-url")

        assert resp.status_code == 200
        auth_url = resp.json()["auth_url"]
        parsed = urllib.parse.urlparse(auth_url)
        assert parsed.netloc == "login.tidal.com"
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        assert params["client_id"] == "tidal-client-id"
        assert params["code_challenge_method"] == "S256"
        assert params["code_challenge"]
        assert params["state"]


class TestTidalCallback:
    """GET /auth/tidal/callback — state validation + token persistence."""

    async def test_valid_state_stores_token_and_redirects_success(
        self,
        client: httpx2.AsyncClient,
        tidal_creds: None,
        clean_tidal_token: None,
    ) -> None:
        state = await mint_state(client)
        exchange = AsyncMock(return_value=_exchanged_token())

        with patch(_EXCHANGE, exchange):
            resp = await client.get(
                "/auth/tidal/callback", params={"code": "auth-code", "state": state}
            )

        assert resp.status_code in (302, 307)
        assert (
            resp.headers["location"]
            == "/settings/integrations?auth=tidal&status=success"
        )
        # The exchange received the code and the server-held PKCE verifier
        # recovered from the state row.
        code, verifier = exchange.await_args.args
        assert code == "auth-code"
        assert isinstance(verifier, str)
        assert verifier
        stored = await load_tidal_token()
        assert stored is not None
        assert stored["access_token"] == "tidal-at"
        assert stored["refresh_token"] == "tidal-rt"

    async def test_bad_state_redirects_error_without_storing(
        self,
        client: httpx2.AsyncClient,
        tidal_creds: None,
        clean_tidal_token: None,
    ) -> None:
        exchange = AsyncMock(return_value=_exchanged_token())

        with patch(_EXCHANGE, exchange):
            resp = await client.get(
                "/auth/tidal/callback",
                params={"code": "auth-code", "state": "bogus-state"},
            )

        assert "status=error" in resp.headers["location"]
        assert "reason=invalid_state" in resp.headers["location"]
        exchange.assert_not_awaited()
        assert await load_tidal_token() is None

    async def test_state_minted_for_spotify_rejected(
        self,
        client: httpx2.AsyncClient,
        tidal_creds: None,
        clean_tidal_token: None,
    ) -> None:
        """The state row's service predicate: a Spotify-minted state must not
        authenticate the Tidal callback."""
        from src.interface.api.routes.auth import _create_state

        state = await _create_state(TEST_USER, "spotify", code_verifier="v")
        exchange = AsyncMock(return_value=_exchanged_token())

        with patch(_EXCHANGE, exchange):
            resp = await client.get(
                "/auth/tidal/callback", params={"code": "auth-code", "state": state}
            )

        assert "reason=invalid_state" in resp.headers["location"]
        exchange.assert_not_awaited()
        assert await load_tidal_token() is None

    async def test_provider_error_redirects_error(
        self, client: httpx2.AsyncClient, tidal_creds: None
    ) -> None:
        resp = await client.get(
            "/auth/tidal/callback", params={"error": "access_denied"}
        )

        assert "status=error" in resp.headers["location"]
        assert "access_denied" in resp.headers["location"]

    async def test_exchange_failure_redirects_error(
        self,
        client: httpx2.AsyncClient,
        tidal_creds: None,
        clean_tidal_token: None,
    ) -> None:
        state = await mint_state(client)
        exchange = AsyncMock(
            side_effect=httpx2.HTTPStatusError(
                "HTTP 400", request=AsyncMock(), response=AsyncMock()
            )
        )

        with patch(_EXCHANGE, exchange):
            resp = await client.get(
                "/auth/tidal/callback", params={"code": "auth-code", "state": state}
            )

        assert "reason=exchange_failed" in resp.headers["location"]
        assert await load_tidal_token() is None


class TestMiddleware:
    """TidalReauthRequiredError → 409 TIDAL_AUTH_REQUIRED envelope."""

    async def test_reauth_error_maps_to_409(self) -> None:
        app = FastAPI()
        register_exception_handlers(app)

        async def boom() -> None:
            raise TidalReauthRequiredError

        app.add_api_route("/boom", boom)

        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/boom")

        assert resp.status_code == 409
        body = resp.json()
        assert body["error"]["code"] == "TIDAL_AUTH_REQUIRED"
        assert "reconnect" in body["error"]["message"].lower()
