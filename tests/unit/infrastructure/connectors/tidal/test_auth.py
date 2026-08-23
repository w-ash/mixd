"""Unit tests for the Tidal OAuth 2.1 auth module (v0.11.3 T4).

Covers PKCE S256 (RFC 7636 fixed test vector), authorization-URL assembly,
the code exchange (public client: client_id in the body, no secret; live
``expires_in`` trusted, never a hardcoded TTL), the rotation-safe refresh
path through the shared single-flight guard (Tidal rotates refresh tokens —
a concurrent second refresh with the same token reads as replay), the
``invalid_grant`` compare-and-delete → ``TidalReauthRequiredError`` path,
and the bearer auth flow's one-forced-refresh-then-replay on 401.
"""

from contextlib import asynccontextmanager
import time
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
import urllib.parse

import httpx2
import pytest

from src.config.settings import CredentialsConfig, settings
from src.domain.exceptions import (
    TidalAuthRequiredError,
    TidalReauthRequiredError,
)
from src.infrastructure.connectors._shared.oauth import compute_pkce_challenge
from src.infrastructure.connectors._shared.token_storage import StoredToken
from src.infrastructure.connectors.tidal.auth import (
    TIDAL_SCOPES,
    TidalBearerAuth,
    TidalTokenManager,
    build_auth_url,
    exchange_code,
)

_UID = "test-user"
_AUTH_MOD = "src.infrastructure.connectors.tidal.auth"
_RT_OLD = "rt-old"

_CLIENT_ID = "tidal-client-id"
_REDIRECT_URI = "https://app.example/auth/tidal/callback"


@pytest.fixture
def tidal_creds():
    creds = CredentialsConfig(
        tidal_client_id=_CLIENT_ID,
        tidal_redirect_uri=_REDIRECT_URI,
    )
    with patch.object(settings, "credentials", creds):
        yield creds


def _token_response(status_code: int = 200, json_body: object = None) -> MagicMock:
    """MagicMock httpx2 response — ``raise_for_status``/``json`` are sync."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body
    if status_code >= 400:
        response.raise_for_status.side_effect = httpx2.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=response
        )
    return response


def _patch_auth_client(response: MagicMock):
    """Patch make_tidal_auth_client; returns the context manager."""
    ctx = patch(f"{_AUTH_MOD}.make_tidal_auth_client")
    return ctx, response


class TestPKCE:
    """RFC 7636 S256 challenge derivation."""

    def test_rfc7636_appendix_b_vector(self):
        # The RFC's own worked example: verifier → S256 challenge.
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        assert (
            compute_pkce_challenge(verifier)
            == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        )

    def test_challenge_is_43_base64url_chars(self):
        challenge = compute_pkce_challenge("some-verifier")
        assert len(challenge) == 43
        assert "=" not in challenge
        assert "+" not in challenge
        assert "/" not in challenge

    def test_distinct_verifiers_distinct_challenges(self):
        assert compute_pkce_challenge("a") != compute_pkce_challenge("b")


class TestBuildAuthUrl:
    """Authorization URL assembly against login.tidal.com."""

    async def test_url_carries_all_oauth_params(self, tidal_creds) -> None:
        create_state = AsyncMock(return_value="state-abc")

        url = await build_auth_url(_UID, MagicMock(), create_state)

        parsed = urllib.parse.urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "login.tidal.com"
        assert parsed.path == "/authorize"
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        assert params["client_id"] == _CLIENT_ID
        assert params["response_type"] == "code"
        assert params["redirect_uri"] == _REDIRECT_URI
        assert params["scope"] == " ".join(TIDAL_SCOPES)
        assert params["state"] == "state-abc"
        assert params["code_challenge_method"] == "S256"
        # The challenge in the URL matches the verifier handed to the state
        # store — the round-trip Tidal will verify at token exchange.
        verifier = create_state.await_args.kwargs["code_verifier"]
        assert params["code_challenge"] == compute_pkce_challenge(verifier)

    async def test_state_minted_for_tidal_service(self, tidal_creds) -> None:
        create_state = AsyncMock(return_value="state-abc")

        await build_auth_url(_UID, MagicMock(), create_state)

        args = create_state.await_args.args
        assert args[0] == _UID
        assert args[1] == "tidal"

    async def test_fresh_verifier_per_attempt(self, tidal_creds) -> None:
        create_state = AsyncMock(return_value="state-abc")

        await build_auth_url(_UID, MagicMock(), create_state)
        await build_auth_url(_UID, MagicMock(), create_state)

        first, second = (
            c.kwargs["code_verifier"] for c in create_state.await_args_list
        )
        assert first != second


class TestExchangeCode:
    """POST auth.tidal.com/v1/oauth2/token — authorization_code + PKCE."""

    async def _exchange(self, response: MagicMock) -> tuple[StoredToken, dict]:
        with patch(f"{_AUTH_MOD}.make_tidal_auth_client") as mock_client:
            post = AsyncMock(return_value=response)
            mock_client.return_value.__aenter__.return_value.post = post
            token = await exchange_code("auth-code", "the-verifier")
        return token, post.await_args.kwargs["data"]

    async def test_public_client_body_no_secret(self, tidal_creds) -> None:
        response = _token_response(
            json_body={
                "access_token": "at-1",
                "refresh_token": "rt-1",
                "token_type": "Bearer",
                "expires_in": 600,
            }
        )

        _, data = await self._exchange(response)

        assert data["grant_type"] == "authorization_code"
        assert data["code"] == "auth-code"
        assert data["code_verifier"] == "the-verifier"
        assert data["client_id"] == _CLIENT_ID
        assert data["redirect_uri"] == _REDIRECT_URI
        assert "client_secret" not in data

    async def test_stored_token_shape_trusts_live_expires_in(self, tidal_creds) -> None:
        response = _token_response(
            json_body={
                "access_token": "at-1",
                "refresh_token": "rt-1",
                "token_type": "Bearer",
                "expires_in": 600,
                "scope": "collection.read",
            }
        )

        before = int(time.time())
        token, _ = await self._exchange(response)
        after = int(time.time())

        assert token["access_token"] == "at-1"
        assert token["refresh_token"] == "rt-1"
        assert token["scope"] == "collection.read"
        # Live expires_in (600) is authoritative — never a hardcoded 3600.
        assert before + 600 <= token["expires_at"] <= after + 600
        authorized_at = token["extra_data"]["authorized_at"]
        assert isinstance(authorized_at, int)
        assert before <= authorized_at <= after

    async def test_missing_expires_in_is_an_error_not_an_invented_ttl(
        self, tidal_creds
    ) -> None:
        # Backlog decision: trust the live token response's TTL; a response
        # without one must fail loudly rather than store an invented expiry.
        response = _token_response(
            json_body={"access_token": "at-1", "refresh_token": "rt-1"}
        )

        with pytest.raises(ValueError, match="expires_in"):
            await self._exchange(response)


def _make_token(
    *,
    expires_at: int | None = None,
    refresh_token: str | None = _RT_OLD,
    extra_data: dict[str, object] | None = None,
    account_name: str | None = None,
) -> StoredToken:
    token = StoredToken(
        access_token="at-old",
        token_type="Bearer",
        expires_at=expires_at if expires_at is not None else int(time.time()) + 3600,
        scope="collection.read",
    )
    if refresh_token is not None:
        token["refresh_token"] = refresh_token
    if extra_data is not None:
        token["extra_data"] = extra_data
    if account_name is not None:
        token["account_name"] = account_name
    return token


def _mock_guard(rotated: StoredToken | None) -> MagicMock:
    guard = MagicMock()
    guard.rotated_token = rotated
    guard.save = AsyncMock()
    return guard


def _patch_single_flight(guard: MagicMock) -> tuple[object, MagicMock]:
    """Patch the shared guard CM to yield ``guard``; returns (patcher, call spy)."""
    spy = MagicMock()

    @asynccontextmanager
    async def _flight(
        service: str,
        user_id: str,
        *,
        current_refresh_token: str,
        lock_timeout_seconds: float,
    ):
        spy(
            service,
            user_id,
            current_refresh_token=current_refresh_token,
            lock_timeout_seconds=lock_timeout_seconds,
        )
        yield guard

    return patch(f"{_AUTH_MOD}.single_flight_token_refresh", _flight), spy


class TestTokenManagerRefresh:
    """Rotation-safe refresh through the shared single-flight guard."""

    @pytest.fixture
    def mock_storage(self) -> AsyncMock:
        storage = AsyncMock()
        storage.load_token = AsyncMock(return_value=None)
        storage.save_token = AsyncMock()
        storage.delete_token = AsyncMock()
        return storage

    @pytest.fixture
    def manager(self, mock_storage: AsyncMock) -> TidalTokenManager:
        return TidalTokenManager(storage=mock_storage, user_id=_UID)

    async def test_fresh_token_returned_without_refresh(
        self, manager: TidalTokenManager, mock_storage: AsyncMock
    ) -> None:
        mock_storage.load_token.return_value = _make_token()

        assert await manager.get_valid_token() == "at-old"
        mock_storage.load_token.assert_awaited_once_with("tidal", _UID)

    async def test_expiry_buffer_triggers_refresh_within_300s(
        self, manager: TidalTokenManager, mock_storage: AsyncMock, tidal_creds
    ) -> None:
        # 100s of validity left is inside the 300s pre-expiry buffer.
        mock_storage.load_token.return_value = _make_token(
            expires_at=int(time.time()) + 100
        )
        rotated = _make_token(expires_at=int(time.time()) + 3600)
        rotated["access_token"] = "at-rotated"
        patcher, _spy = _patch_single_flight(_mock_guard(rotated))

        with patcher:
            assert await manager.get_valid_token() == "at-rotated"

    async def test_rotated_token_adopted_without_post(
        self, manager: TidalTokenManager, mock_storage: AsyncMock, tidal_creds
    ) -> None:
        """Another flight already rotated — adopt its token, never POST."""
        mock_storage.load_token.return_value = _make_token(
            expires_at=int(time.time()) - 60
        )
        rotated = _make_token(expires_at=int(time.time()) + 3600)
        rotated["access_token"] = "at-rotated"
        rotated["refresh_token"] = "rt-rotated"
        guard = _mock_guard(rotated)
        patcher, spy = _patch_single_flight(guard)

        with (
            patcher,
            patch(f"{_AUTH_MOD}.make_tidal_auth_client") as mock_client,
        ):
            token = await manager.get_valid_token()

        assert token == "at-rotated"
        mock_client.assert_not_called()
        guard.save.assert_not_awaited()
        spy.assert_called_once_with(
            "tidal",
            _UID,
            current_refresh_token="rt-old",
            # Waiters must outlast a healthy winner's refresh POST: the
            # configured request timeout plus headroom.
            lock_timeout_seconds=settings.api.tidal.request_timeout + 5.0,
        )

    async def test_winner_posts_and_persists_rotation_via_guard(
        self, manager: TidalTokenManager, mock_storage: AsyncMock, tidal_creds
    ) -> None:
        mock_storage.load_token.return_value = _make_token(
            expires_at=int(time.time()) - 60,
            extra_data={"authorized_at": 1_700_000_000},
            account_name="Wash",
        )
        guard = _mock_guard(None)
        patcher, _spy = _patch_single_flight(guard)
        response = _token_response(
            json_body={
                "access_token": "at-new",
                "refresh_token": "rt-new",
                "token_type": "Bearer",
                "expires_in": 43200,
            }
        )

        with (
            patcher,
            patch(f"{_AUTH_MOD}.make_tidal_auth_client") as mock_client,
        ):
            post = AsyncMock(return_value=response)
            mock_client.return_value.__aenter__.return_value.post = post
            token = await manager.get_valid_token()

        assert token == "at-new"
        data = post.await_args.kwargs["data"]
        assert data["grant_type"] == "refresh_token"
        assert data["refresh_token"] == "rt-old"
        assert data["client_id"] == _CLIENT_ID
        assert "client_secret" not in data
        # The rotated pair is persisted through the guard's lock-holding
        # session — never a bare save_token racing the flight.
        guard.save.assert_awaited_once()
        saved = guard.save.await_args.args[0]
        assert saved["access_token"] == "at-new"
        assert saved["refresh_token"] == "rt-new"
        # Ours, never Tidal's: carried forward verbatim across the rotation.
        assert saved["extra_data"] == {"authorized_at": 1_700_000_000}
        assert saved["account_name"] == "Wash"
        mock_storage.save_token.assert_not_awaited()

    async def test_invalid_grant_compare_deletes_and_raises_reauth(
        self, manager: TidalTokenManager, mock_storage: AsyncMock, tidal_creds
    ) -> None:
        stored = _make_token(expires_at=int(time.time()) - 60)
        mock_storage.load_token.return_value = stored
        guard = _mock_guard(None)
        patcher, _spy = _patch_single_flight(guard)
        response = _token_response(400, {"error": "invalid_grant"})

        with (
            patcher,
            patch(f"{_AUTH_MOD}.make_tidal_auth_client") as mock_client,
        ):
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=response
            )
            with pytest.raises(TidalReauthRequiredError):
                await manager.get_valid_token()

        mock_storage.delete_token.assert_awaited_once_with("tidal", _UID)
        guard.save.assert_not_awaited()

    async def test_invalid_grant_skips_delete_when_stored_rotated(
        self, manager: TidalTokenManager, mock_storage: AsyncMock, tidal_creds
    ) -> None:
        """A stale flight must not destroy a newer grant: only delete when
        the stored refresh token is still the one that just failed."""
        expired = _make_token(expires_at=int(time.time()) - 60)
        newer = _make_token(refresh_token="rt-NEWER")
        # First load feeds get_valid_token; the compare re-load sees the
        # newer grant another flight persisted in between.
        mock_storage.load_token.side_effect = [expired, newer]
        guard = _mock_guard(None)
        patcher, _spy = _patch_single_flight(guard)
        response = _token_response(400, {"error": "invalid_grant"})

        with (
            patcher,
            patch(f"{_AUTH_MOD}.make_tidal_auth_client") as mock_client,
        ):
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=response
            )
            with pytest.raises(TidalReauthRequiredError):
                await manager.get_valid_token()

        mock_storage.delete_token.assert_not_awaited()

    async def test_other_refresh_failures_propagate_without_delete(
        self, manager: TidalTokenManager, mock_storage: AsyncMock, tidal_creds
    ) -> None:
        mock_storage.load_token.return_value = _make_token(
            expires_at=int(time.time()) - 60
        )
        guard = _mock_guard(None)
        patcher, _spy = _patch_single_flight(guard)
        response = _token_response(500, {"error": "server_error"})

        with (
            patcher,
            patch(f"{_AUTH_MOD}.make_tidal_auth_client") as mock_client,
        ):
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=response
            )
            with pytest.raises(httpx2.HTTPStatusError):
                await manager.get_valid_token()

        mock_storage.delete_token.assert_not_awaited()
        guard.save.assert_not_awaited()

    async def test_no_token_raises_auth_required(
        self, manager: TidalTokenManager, mock_storage: AsyncMock
    ) -> None:
        mock_storage.load_token.return_value = None

        with pytest.raises(TidalAuthRequiredError):
            await manager.get_valid_token()

    async def test_expired_without_refresh_token_raises_reauth(
        self, manager: TidalTokenManager, mock_storage: AsyncMock
    ) -> None:
        mock_storage.load_token.return_value = _make_token(
            expires_at=int(time.time()) - 60, refresh_token=None
        )

        with pytest.raises(TidalReauthRequiredError):
            await manager.get_valid_token()


class TestBearerAuth:
    """401 → one forced refresh, then replay the request."""

    def _manager_stub(self) -> tuple[TidalTokenManager, AsyncMock, AsyncMock]:
        get_valid = AsyncMock(return_value="at-old")
        force = AsyncMock(return_value="at-new")
        stub = MagicMock()
        stub.get_valid_token = get_valid
        stub.force_refresh = force
        return cast("TidalTokenManager", stub), get_valid, force

    async def test_injects_bearer_token(self) -> None:
        manager, _get_valid, force = self._manager_stub()
        auth = TidalBearerAuth(manager)
        request = httpx2.Request("GET", "https://openapi.tidal.com/v2/tracks")

        flow = auth.async_auth_flow(request)
        sent = await anext(flow)

        assert sent.headers["Authorization"] == "Bearer at-old"
        force.assert_not_awaited()
        await flow.aclose()

    async def test_401_forces_one_refresh_and_replays(self) -> None:
        manager, _get_valid, force = self._manager_stub()
        auth = TidalBearerAuth(manager)
        request = httpx2.Request("GET", "https://openapi.tidal.com/v2/tracks")

        flow = auth.async_auth_flow(request)
        _ = await anext(flow)
        response_401 = MagicMock()
        response_401.status_code = 401
        replayed = await flow.asend(response_401)

        assert replayed.headers["Authorization"] == "Bearer at-new"
        force.assert_awaited_once()
        # One replay only: a second 401 terminates the flow.
        with pytest.raises(StopAsyncIteration):
            await flow.asend(response_401)

    async def test_non_401_does_not_refresh(self) -> None:
        manager, _get_valid, force = self._manager_stub()
        auth = TidalBearerAuth(manager)
        request = httpx2.Request("GET", "https://openapi.tidal.com/v2/tracks")

        flow = auth.async_auth_flow(request)
        _ = await anext(flow)
        response_ok = MagicMock()
        response_ok.status_code = 200
        with pytest.raises(StopAsyncIteration):
            await flow.asend(response_ok)

        force.assert_not_awaited()
