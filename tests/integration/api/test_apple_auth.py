"""Integration tests for the in-app Apple Music MusicKit connect routes.

The v0.11.x bridge page is gone: the SPA runs ``MusicKit.authorize()`` itself.
The backend surface is two authenticated JSON routes — ``GET
/api/v1/connectors/apple_music/musickit-config`` hands the browser-safe
developer token to the app page, and ``POST
/api/v1/connectors/apple_music/token`` persists the resulting Music User
Token under the authenticated user (``get_current_user_id``, like every other
API route — no CSRF state). The developer token is minted with a throwaway EC
P-256 key; no Apple network calls happen anywhere — the storefront fetch is
patched at the ``AppleMusicAPIClient`` class.

The Bearer requirement itself (401 without auth) is enforced by
``NeonAuthMiddleware`` — covered in ``test_auth_middleware.py`` and the gate
unit tests, since the default test app runs with auth disabled.
"""

from collections.abc import Generator
from datetime import timedelta
import time
from unittest.mock import AsyncMock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import httpx2
import jwt
import pytest

from src.config.settings import CredentialsConfig, settings
from src.infrastructure.connectors._shared.token_storage import StoredToken
from src.infrastructure.connectors.apple_music.client import AppleMusicAPIClient
from src.infrastructure.connectors.apple_music.models import AppleMusicStorefront
from src.infrastructure.persistence.repositories.token_storage import (
    DatabaseTokenStorage,
)

TEAM_ID = "TEAMID9999"
KEY_ID = "KEYID99999"
# ``get_current_user_id`` falls back to DEFAULT_USER_ID when auth is disabled,
# which is the test-app configuration.
TEST_USER = "default"
MUT_TTL_SECONDS = timedelta(days=182).total_seconds()

CONFIG_URL = "/api/v1/connectors/apple_music/musickit-config"
TOKEN_URL = "/api/v1/connectors/apple_music/token"


@pytest.fixture(scope="module")
def key_pair() -> tuple[str, str]:
    """Throwaway EC P-256 key pair as (private_pem, public_pem)."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


@pytest.fixture
def apple_creds(key_pair: tuple[str, str]) -> Generator[None]:
    """Fully configured Apple Music credentials on the live settings object."""
    creds = CredentialsConfig(
        apple_team_id=TEAM_ID,
        apple_key_id=KEY_ID,
        apple_private_key=key_pair[0],
    )
    with patch.object(settings, "credentials", creds):
        yield


@pytest.fixture
def no_apple_creds() -> Generator[None]:
    """Apple Music credentials explicitly absent."""
    creds = CredentialsConfig(
        apple_team_id="",
        apple_key_id="",
        apple_private_key="",
    )
    with patch.object(settings, "credentials", creds):
        yield


async def load_apple_token() -> StoredToken | None:
    return await DatabaseTokenStorage().load_token("apple_music", TEST_USER)


@pytest.fixture
async def clean_apple_token(client: httpx2.AsyncClient) -> None:
    """The oauth_tokens table is preserved across tests — start from nothing."""
    await DatabaseTokenStorage().delete_token("apple_music", TEST_USER)


class TestAuthUrlGate:
    """GET /api/v1/connectors/apple_music/auth-url — no longer served.

    ``browser_bridge`` connects in-app now; the generic auth-url route is
    back to its pre-Apple gate (``oauth`` + a registered ``build_auth_url``).
    """

    async def test_auth_url_returns_400_for_browser_bridge(
        self, client: httpx2.AsyncClient
    ) -> None:
        resp = await client.get("/api/v1/connectors/apple_music/auth-url")

        assert resp.status_code == 400

    async def test_auth_url_still_serves_oauth_connectors(
        self, client: httpx2.AsyncClient
    ) -> None:
        """Reverting the browser_bridge widening must not break OAuth."""
        resp = await client.get("/api/v1/connectors/spotify/auth-url")

        assert resp.status_code == 200
        assert resp.json()["auth_url"]


class TestMusickitConfig:
    """GET /api/v1/connectors/apple_music/musickit-config — developer token."""

    async def test_returns_verifiable_developer_token(
        self,
        client: httpx2.AsyncClient,
        apple_creds: None,
        key_pair: tuple[str, str],
    ) -> None:
        resp = await client.get(CONFIG_URL)

        assert resp.status_code == 200
        token = resp.json()["developer_token"]
        claims = jwt.decode(token, key_pair[1], algorithms=["ES256"])
        assert claims["iss"] == TEAM_ID

    async def test_private_key_never_leaves_the_server(
        self, client: httpx2.AsyncClient, apple_creds: None
    ) -> None:
        resp = await client.get(CONFIG_URL)

        assert resp.status_code == 200
        assert "PRIVATE KEY" not in resp.text

    async def test_unconfigured_returns_clean_503(
        self, client: httpx2.AsyncClient, no_apple_creds: None
    ) -> None:
        resp = await client.get(CONFIG_URL)

        assert resp.status_code == 503
        assert "not configured" in resp.text.lower()
        assert "Traceback" not in resp.text


class TestTokenPersistence:
    """POST /api/v1/connectors/apple_music/token — MUT storage."""

    async def test_stores_mut_under_authenticated_user(
        self, client: httpx2.AsyncClient, clean_apple_token: None
    ) -> None:
        with patch.object(
            AppleMusicAPIClient, "get_storefront", AsyncMock(return_value=None)
        ):
            resp = await client.post(TOKEN_URL, json={"music_user_token": "fake-mut"})

        assert resp.status_code == 204
        stored = await load_apple_token()
        assert stored is not None
        assert stored["access_token"] == "fake-mut"
        assert stored["token_type"] == "music_user_token"
        # Apple MUT fixed lifetime ~6 months, no refresh.
        assert stored["expires_at"] == pytest.approx(
            time.time() + MUT_TTL_SECONDS, abs=300
        )
        assert stored["extra_data"]["authorized_at"] == pytest.approx(
            time.time(), abs=300
        )

    async def test_storefront_recorded_when_fetch_succeeds(
        self, client: httpx2.AsyncClient, clean_apple_token: None
    ) -> None:
        with patch.object(
            AppleMusicAPIClient,
            "get_storefront",
            AsyncMock(return_value=AppleMusicStorefront(id="us")),
        ):
            resp = await client.post(TOKEN_URL, json={"music_user_token": "fake-mut"})

        assert resp.status_code == 204
        stored = await load_apple_token()
        assert stored is not None
        assert stored["extra_data"]["storefront"] == "us"

    async def test_storefront_failure_does_not_fail_connect(
        self, client: httpx2.AsyncClient, clean_apple_token: None
    ) -> None:
        with patch.object(
            AppleMusicAPIClient,
            "get_storefront",
            AsyncMock(side_effect=RuntimeError("apple is down")),
        ):
            resp = await client.post(TOKEN_URL, json={"music_user_token": "fake-mut"})

        assert resp.status_code == 204
        stored = await load_apple_token()
        assert stored is not None
        assert stored["access_token"] == "fake-mut"
        assert "storefront" not in stored.get("extra_data", {})

    async def test_empty_mut_rejected(
        self, client: httpx2.AsyncClient, clean_apple_token: None
    ) -> None:
        resp = await client.post(TOKEN_URL, json={"music_user_token": ""})

        assert resp.status_code == 422


class TestDisconnect:
    """DELETE /api/v1/connectors/apple_music/token — round-trip."""

    async def test_apple_music_can_disconnect(
        self, client: httpx2.AsyncClient, clean_apple_token: None
    ) -> None:
        with patch.object(
            AppleMusicAPIClient, "get_storefront", AsyncMock(return_value=None)
        ):
            connect = await client.post(
                TOKEN_URL, json={"music_user_token": "fake-mut"}
            )
        assert connect.status_code == 204

        resp = await client.delete(TOKEN_URL)

        assert resp.status_code == 204
        assert await load_apple_token() is None
