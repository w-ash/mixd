"""Integration tests for the Apple Music MusicKit JS bridge routes.

Covers the browser bridge page (``GET /auth/apple/authorize``), the Music
User Token persistence endpoint (``POST /api/v1/connectors/apple_music/token``),
and the widened auth-url gate that lets ``browser_bridge`` connectors mint a
connect URL. The developer token is minted with a throwaway EC P-256 key;
no Apple network calls happen anywhere — the storefront fetch is patched at
the ``AppleMusicAPIClient`` class.
"""

from collections.abc import Generator
from datetime import timedelta
import re
import time
from unittest.mock import AsyncMock, patch
import urllib.parse

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
MUSICKIT_CDN = "https://js-cdn.music.apple.com/musickit/v3/musickit.js"
# ``get_current_user_id`` falls back to DEFAULT_USER_ID when auth is disabled,
# which is the test-app configuration.
TEST_USER = "default"
MUT_TTL_SECONDS = timedelta(days=182).total_seconds()

_JWT_RE = re.compile(r"eyJ[\w\-]+\.[\w\-]+\.[\w\-]+")


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


async def mint_state(client: httpx2.AsyncClient) -> str:
    """Create a real CSRF state row via the public auth-url endpoint."""
    resp = await client.get("/api/v1/connectors/apple_music/auth-url")
    assert resp.status_code == 200, resp.text
    auth_url = resp.json()["auth_url"]
    query = urllib.parse.urlparse(auth_url).query
    return urllib.parse.parse_qs(query)["state"][0]


async def load_apple_token() -> StoredToken | None:
    return await DatabaseTokenStorage().load_token("apple_music", TEST_USER)


@pytest.fixture
async def clean_apple_token(client: httpx2.AsyncClient) -> None:
    """The oauth_tokens table is preserved across tests — start from nothing."""
    await DatabaseTokenStorage().delete_token("apple_music", TEST_USER)


class TestAuthUrlGate:
    """GET /api/v1/connectors/apple_music/auth-url — browser_bridge allowed."""

    async def test_auth_url_returns_bridge_url_with_state(
        self, client: httpx2.AsyncClient
    ) -> None:
        resp = await client.get("/api/v1/connectors/apple_music/auth-url")

        assert resp.status_code == 200
        auth_url = resp.json()["auth_url"]
        assert auth_url.startswith("/auth/apple/authorize?")
        state = urllib.parse.parse_qs(urllib.parse.urlparse(auth_url).query)["state"]
        assert state[0]


class TestBridgePage:
    """GET /auth/apple/authorize — the MusicKit JS bridge page."""

    async def test_renders_musickit_page_with_dev_token(
        self,
        client: httpx2.AsyncClient,
        apple_creds: None,
        key_pair: tuple[str, str],
    ) -> None:
        state = await mint_state(client)
        resp = await client.get(f"/auth/apple/authorize?state={state}")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        # Documented cause of authorize() 403s when stricter — must be exact.
        assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        body = resp.text
        assert MUSICKIT_CDN in body
        # The embedded developer token is a verifiable ES256 JWT.
        jwts = _JWT_RE.findall(body)
        assert jwts, "no developer-token JWT embedded in the bridge page"
        claims = jwt.decode(jwts[0], key_pair[1], algorithms=["ES256"])
        assert claims["iss"] == TEAM_ID
        # The signing key itself must never reach the browser.
        assert "PRIVATE KEY" not in body
        # The page posts to the token endpoint and carries the state through.
        assert "/api/v1/connectors/apple_music/token" in body
        assert state in body
        # musickitloaded may have fired before this listener registers (cached
        # script): the page must run the routine synchronously when
        # window.MusicKit already exists.
        assert "if (window.MusicKit)" in body
        assert 'addEventListener("musickitloaded"' in body

    async def test_unconfigured_returns_clean_error(
        self, client: httpx2.AsyncClient, no_apple_creds: None
    ) -> None:
        resp = await client.get("/auth/apple/authorize?state=whatever")

        assert resp.status_code == 503
        assert "not configured" in resp.text.lower()
        assert "Traceback" not in resp.text
        # Every bridge response pins the policy, the error page included.
        assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"


class TestTokenPersistence:
    """POST /api/v1/connectors/apple_music/token — MUT storage."""

    async def test_stores_mut_with_six_month_expiry(
        self, client: httpx2.AsyncClient, clean_apple_token: None
    ) -> None:
        state = await mint_state(client)

        with patch.object(
            AppleMusicAPIClient, "get_storefront", AsyncMock(return_value=None)
        ):
            resp = await client.post(
                "/api/v1/connectors/apple_music/token",
                json={"music_user_token": "fake-mut", "state": state},
            )

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
        state = await mint_state(client)

        with patch.object(
            AppleMusicAPIClient,
            "get_storefront",
            AsyncMock(return_value=AppleMusicStorefront(id="us")),
        ):
            resp = await client.post(
                "/api/v1/connectors/apple_music/token",
                json={"music_user_token": "fake-mut", "state": state},
            )

        assert resp.status_code == 204
        stored = await load_apple_token()
        assert stored is not None
        assert stored["extra_data"]["storefront"] == "us"

    async def test_storefront_failure_does_not_fail_connect(
        self, client: httpx2.AsyncClient, clean_apple_token: None
    ) -> None:
        state = await mint_state(client)

        with patch.object(
            AppleMusicAPIClient,
            "get_storefront",
            AsyncMock(side_effect=RuntimeError("apple is down")),
        ):
            resp = await client.post(
                "/api/v1/connectors/apple_music/token",
                json={"music_user_token": "fake-mut", "state": state},
            )

        assert resp.status_code == 204
        stored = await load_apple_token()
        assert stored is not None
        assert stored["access_token"] == "fake-mut"
        assert "storefront" not in stored.get("extra_data", {})

    async def test_bad_state_rejected(
        self, client: httpx2.AsyncClient, clean_apple_token: None
    ) -> None:
        resp = await client.post(
            "/api/v1/connectors/apple_music/token",
            json={"music_user_token": "fake-mut", "state": "bogus-state"},
        )

        assert resp.status_code == 400
        assert await load_apple_token() is None

    async def test_state_is_single_use(
        self, client: httpx2.AsyncClient, clean_apple_token: None
    ) -> None:
        state = await mint_state(client)
        payload = {"music_user_token": "fake-mut", "state": state}

        with patch.object(
            AppleMusicAPIClient, "get_storefront", AsyncMock(return_value=None)
        ):
            first = await client.post(
                "/api/v1/connectors/apple_music/token", json=payload
            )
            second = await client.post(
                "/api/v1/connectors/apple_music/token", json=payload
            )

        assert first.status_code == 204
        assert second.status_code == 400

    async def test_state_minted_for_another_service_rejected(
        self, client: httpx2.AsyncClient, clean_apple_token: None
    ) -> None:
        """A CSRF state row is bound to its service: a Spotify state must not
        authenticate the Apple token endpoint."""
        from src.interface.api.routes.auth import _create_state

        state = await _create_state(TEST_USER, "spotify")

        resp = await client.post(
            "/api/v1/connectors/apple_music/token",
            json={"music_user_token": "fake-mut", "state": state},
        )

        assert resp.status_code == 400
        assert await load_apple_token() is None

    async def test_empty_mut_rejected(
        self, client: httpx2.AsyncClient, clean_apple_token: None
    ) -> None:
        state = await mint_state(client)
        resp = await client.post(
            "/api/v1/connectors/apple_music/token",
            json={"music_user_token": "", "state": state},
        )

        assert resp.status_code == 422


class TestDisconnect:
    """DELETE /api/v1/connectors/apple_music/token — gate widened."""

    async def test_apple_music_can_disconnect(
        self, client: httpx2.AsyncClient, clean_apple_token: None
    ) -> None:
        state = await mint_state(client)
        with patch.object(
            AppleMusicAPIClient, "get_storefront", AsyncMock(return_value=None)
        ):
            connect = await client.post(
                "/api/v1/connectors/apple_music/token",
                json={"music_user_token": "fake-mut", "state": state},
            )
        assert connect.status_code == 204

        resp = await client.delete("/api/v1/connectors/apple_music/token")

        assert resp.status_code == 204
        assert await load_apple_token() is None
