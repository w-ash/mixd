"""Unit tests for the Tidal CLI auth flows (v0.11.3 T6).

Covers ``run_device_auth`` — the primary CLI flow against Tidal's
*undocumented* ``POST /v1/oauth2/device_authorization`` endpoint — and the
``run_browser_auth`` localhost-redirect fallback.

Device flow (RFC 8628 shape, scripted through ``httpx2.MockTransport``):
``authorization_pending`` keeps polling at ``interval``; ``slow_down``
widens the interval by 5s; ``expired_token`` raises a clean typed error
telling the user to rerun; success builds the same StoredToken shape as
``exchange_code`` (pair + live-``expires_in`` expiry + ``authorized_at``)
and saves it. A 404 or an unsupported-shaped 400 on the
device_authorization POST raises ``DeviceCodeUnsupportedError`` so the CLI
can auto-fall back. Sleeps are injected and recorded — no wall-clock.

Browser fallback: the one-shot HTTPServer capture is patched at the
``capture_loopback_redirect`` seam (Spotify's ``run_browser_auth`` has no
real-server test either — the server interaction is mocked everywhere);
the state check, PKCE verifier round-trip, exchange, and save are asserted
directly, and the pure port/state helpers are unit-tested.
"""

import time
from unittest.mock import ANY, AsyncMock, patch
import urllib.parse

import httpx2
import pytest

from src.config.settings import CredentialsConfig, settings
from src.infrastructure.connectors._shared.oauth import compute_pkce_challenge
from src.infrastructure.connectors._shared.token_storage import StoredToken
from src.infrastructure.connectors.tidal.device_auth import (
    DeviceAuthorization,
    DeviceCodeExpiredError,
    DeviceCodeUnsupportedError,
    _redirect_port,
    run_browser_auth,
    run_device_auth,
)

_UID = "test-user"
_AUTH_MOD = "src.infrastructure.connectors.tidal.device_auth"
_CLIENT_ID = "tidal-client-id"
_REDIRECT_URI = "http://127.0.0.1:8899/callback"

_DEVICE_AUTH_PATH = "/v1/oauth2/device_authorization"
_TOKEN_PATH = "/v1/oauth2/token"

_GRANT_JSON = {
    "device_code": "dc-1",
    "user_code": "ABCDE",
    "verification_uri": "https://link.tidal.com",
    "verification_uri_complete": "https://link.tidal.com/ABCDE",
    "expires_in": 300,
    "interval": 5,
}

_TOKEN_JSON = {
    "access_token": "at-device",
    "refresh_token": "rt-device",
    "token_type": "Bearer",
    "expires_in": 43200,
    "scope": "collection.read",
}


@pytest.fixture
def tidal_creds():
    creds = CredentialsConfig(
        tidal_client_id=_CLIENT_ID,
        tidal_redirect_uri=_REDIRECT_URI,
    )
    with patch.object(settings, "credentials", creds):
        yield creds


@pytest.fixture
def mock_storage() -> AsyncMock:
    storage = AsyncMock()
    storage.save_token = AsyncMock()
    return storage


class _ScriptedAuthServer:
    """MockTransport handler scripting the device-auth POST + token polls."""

    def __init__(
        self,
        device_response: tuple[int, object],
        token_responses: list[tuple[int, object]],
    ) -> None:
        self._device_response = device_response
        self._token_responses = list(token_responses)
        self.token_requests: list[dict[str, str]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.path == _DEVICE_AUTH_PATH:
            status, body = self._device_response
            return httpx2.Response(status, json=body)
        assert request.url.path == _TOKEN_PATH
        form = urllib.parse.parse_qs(request.content.decode())
        self.token_requests.append({k: v[0] for k, v in form.items()})
        status, body = self._token_responses.pop(0)
        return httpx2.Response(status, json=body)


def _patch_transport(server: _ScriptedAuthServer):
    """Patch make_tidal_auth_client to serve the scripted transport."""

    def _make_client() -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.MockTransport(server),
            base_url="https://auth.tidal.com",
        )

    return patch(f"{_AUTH_MOD}.make_tidal_auth_client", _make_client)


def _sleep_recorder() -> tuple[list[float], object]:
    sleeps: list[float] = []

    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return sleeps, _sleep


class TestRunDeviceAuth:
    """Primary CLI flow: undocumented device_authorization + token polling."""

    async def test_pending_then_success_saves_token_pair(
        self, tidal_creds, mock_storage: AsyncMock
    ) -> None:
        server = _ScriptedAuthServer(
            (200, _GRANT_JSON),
            [
                (400, {"error": "authorization_pending"}),
                (400, {"error": "authorization_pending"}),
                (200, _TOKEN_JSON),
            ],
        )
        sleeps, sleep = _sleep_recorder()
        shown: list[DeviceAuthorization] = []

        before = int(time.time())
        with _patch_transport(server):
            token = await run_device_auth(
                mock_storage, _UID, on_verification=shown.append, sleep=sleep
            )
        after = int(time.time())

        # The user-facing verification handoff carried the code + URI.
        assert [g.user_code for g in shown] == ["ABCDE"]
        assert shown[0].verification_uri == "https://link.tidal.com"
        assert shown[0].verification_uri_complete == "https://link.tidal.com/ABCDE"

        # Poll pacing: one interval-length sleep before each of the 3 polls.
        assert sleeps == [5, 5, 5]

        # Every poll used the device grant with the public-client body.
        assert len(server.token_requests) == 3
        for form in server.token_requests:
            assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code"
            assert form["device_code"] == "dc-1"
            assert form["client_id"] == _CLIENT_ID
            assert "client_secret" not in form

        # StoredToken shape mirrors exchange_code: pair + live expiry +
        # authorized_at, persisted under ("tidal", user).
        assert token["access_token"] == "at-device"
        assert token["refresh_token"] == "rt-device"
        assert before + 43200 <= token["expires_at"] <= after + 43200
        authorized_at = token["extra_data"]["authorized_at"]
        assert isinstance(authorized_at, int)
        assert before <= authorized_at <= after
        mock_storage.save_token.assert_awaited_once_with("tidal", _UID, token)

    async def test_slow_down_widens_interval_by_five(
        self, tidal_creds, mock_storage: AsyncMock
    ) -> None:
        server = _ScriptedAuthServer(
            (200, _GRANT_JSON),
            [
                (400, {"error": "slow_down"}),
                (200, _TOKEN_JSON),
            ],
        )
        sleeps, sleep = _sleep_recorder()

        with _patch_transport(server):
            _ = await run_device_auth(mock_storage, _UID, sleep=sleep)

        # First poll after the grant's interval (5); slow_down widens the
        # pace to 10 for every subsequent poll.
        assert sleeps == [5, 10]

    async def test_expired_token_raises_clean_typed_error(
        self, tidal_creds, mock_storage: AsyncMock
    ) -> None:
        server = _ScriptedAuthServer(
            (200, _GRANT_JSON),
            [(400, {"error": "expired_token"})],
        )
        _sleeps, sleep = _sleep_recorder()

        with _patch_transport(server):
            with pytest.raises(DeviceCodeExpiredError, match="again"):
                await run_device_auth(mock_storage, _UID, sleep=sleep)

        mock_storage.save_token.assert_not_awaited()

    async def test_404_on_device_authorization_raises_unsupported(
        self, tidal_creds, mock_storage: AsyncMock
    ) -> None:
        server = _ScriptedAuthServer((404, {"error": "not_found"}), [])

        with _patch_transport(server):
            with pytest.raises(DeviceCodeUnsupportedError):
                await run_device_auth(mock_storage, _UID)

        mock_storage.save_token.assert_not_awaited()

    async def test_unsupported_shaped_400_raises_unsupported(
        self, tidal_creds, mock_storage: AsyncMock
    ) -> None:
        server = _ScriptedAuthServer((400, {"error": "unsupported_grant_type"}), [])

        with _patch_transport(server):
            with pytest.raises(DeviceCodeUnsupportedError):
                await run_device_auth(mock_storage, _UID)

    async def test_invalid_request_400_surfaces_not_fallback(
        self, tidal_creds, mock_storage: AsyncMock
    ) -> None:
        # invalid_request is a NAMED flow error — the endpoint exists and
        # parsed the request; falling back would silently mask a bug in the
        # request we sent. It must surface, never DeviceCodeUnsupportedError.
        server = _ScriptedAuthServer((400, {"error": "invalid_request"}), [])

        with _patch_transport(server):
            with pytest.raises(httpx2.HTTPStatusError):
                await run_device_auth(mock_storage, _UID)

        mock_storage.save_token.assert_not_awaited()

    async def test_other_device_authorization_failure_propagates(
        self, tidal_creds, mock_storage: AsyncMock
    ) -> None:
        # A 500 is an outage, not "endpoint absent" — it must NOT trigger
        # the browser fallback, which would mask a transient failure.
        server = _ScriptedAuthServer((500, {"error": "server_error"}), [])

        with _patch_transport(server):
            with pytest.raises(httpx2.HTTPStatusError):
                await run_device_auth(mock_storage, _UID)

    async def test_denied_authorization_fails_cleanly(
        self, tidal_creds, mock_storage: AsyncMock
    ) -> None:
        server = _ScriptedAuthServer(
            (200, _GRANT_JSON),
            [(400, {"error": "access_denied"})],
        )
        _sleeps, sleep = _sleep_recorder()

        with _patch_transport(server):
            with pytest.raises(RuntimeError, match="denied"):
                await run_device_auth(mock_storage, _UID, sleep=sleep)

        mock_storage.save_token.assert_not_awaited()


def _stored() -> StoredToken:
    return StoredToken(
        access_token="at-browser",
        refresh_token="rt-browser",
        token_type="Bearer",
        expires_at=int(time.time()) + 43200,
        extra_data={"authorized_at": int(time.time())},
    )


class TestRunBrowserAuth:
    """Localhost-redirect fallback: PKCE held in-process, one-shot server."""

    async def test_success_exchanges_code_with_local_verifier_and_saves(
        self, tidal_creds, mock_storage: AsyncMock
    ) -> None:
        captured_urls: list[str] = []
        captured_ports: list[int] = []

        def fake_capture(
            auth_url: str, port: int, *, service_label: str
        ) -> dict[str, str]:
            # Echo back the state the flow embedded in its authorize URL —
            # what Tidal's redirect would do.
            captured_urls.append(auth_url)
            captured_ports.append(port)
            params = urllib.parse.parse_qs(urllib.parse.urlparse(auth_url).query)
            return {"code": "auth-code-1", "state": params["state"][0]}

        exchange = AsyncMock(return_value=_stored())

        with (
            patch(f"{_AUTH_MOD}.capture_loopback_redirect", fake_capture),
            patch(f"{_AUTH_MOD}.exchange_code", exchange),
        ):
            token = await run_browser_auth(mock_storage, _UID)

        # Port parsed from the configured tidal_redirect_uri.
        assert captured_ports == [8899]

        # The authorize URL carries the PKCE challenge of the verifier that
        # was held in-process and handed to exchange_code — never a
        # DBOAuthState round-trip.
        params = {
            k: v[0]
            for k, v in urllib.parse.parse_qs(
                urllib.parse.urlparse(captured_urls[0]).query
            ).items()
        }
        assert params["client_id"] == _CLIENT_ID
        assert params["redirect_uri"] == _REDIRECT_URI
        assert params["code_challenge_method"] == "S256"
        # No CLI-specific URI configured: the exchange echoes the primary
        # redirect (the one embedded in the authorize URL).
        exchange.assert_awaited_once_with(
            "auth-code-1", ANY, redirect_uri=_REDIRECT_URI
        )
        verifier = exchange.await_args.args[1]
        assert compute_pkce_challenge(verifier) == params["code_challenge"]

        assert token["access_token"] == "at-browser"
        mock_storage.save_token.assert_awaited_once_with("tidal", _UID, token)

    async def test_cli_redirect_uri_overrides_primary_for_the_fallback(
        self, mock_storage: AsyncMock
    ) -> None:
        # The primary TIDAL_REDIRECT_URI points at the web callback (Vite
        # origin) — no local listener can bind it. The optional
        # TIDAL_CLI_REDIRECT_URI carries the loopback URI the one-shot
        # server derives its port from, and the SAME URI must ride the
        # authorize URL and the token exchange (OAuth requires the match).
        cli_uri = "http://127.0.0.1:9911/cli-callback"
        creds = CredentialsConfig(
            tidal_client_id=_CLIENT_ID,
            tidal_redirect_uri="http://localhost:5173/auth/tidal/callback",
            tidal_cli_redirect_uri=cli_uri,
        )
        captured_ports: list[int] = []
        captured_urls: list[str] = []

        def fake_capture(
            auth_url: str, port: int, *, service_label: str
        ) -> dict[str, str]:
            captured_urls.append(auth_url)
            captured_ports.append(port)
            params = urllib.parse.parse_qs(urllib.parse.urlparse(auth_url).query)
            return {"code": "auth-code-2", "state": params["state"][0]}

        exchange = AsyncMock(return_value=_stored())

        with (
            patch.object(settings, "credentials", creds),
            patch(f"{_AUTH_MOD}.capture_loopback_redirect", fake_capture),
            patch(f"{_AUTH_MOD}.exchange_code", exchange),
        ):
            _ = await run_browser_auth(mock_storage, _UID)

        assert captured_ports == [9911]
        params = {
            k: v[0]
            for k, v in urllib.parse.parse_qs(
                urllib.parse.urlparse(captured_urls[0]).query
            ).items()
        }
        assert params["redirect_uri"] == cli_uri
        exchange.assert_awaited_once_with("auth-code-2", ANY, redirect_uri=cli_uri)

    async def test_state_mismatch_rejected_without_exchange(
        self, tidal_creds, mock_storage: AsyncMock
    ) -> None:
        def fake_capture(
            auth_url: str, port: int, *, service_label: str
        ) -> dict[str, str]:
            return {"code": "auth-code-1", "state": "not-the-minted-state"}

        exchange = AsyncMock()

        with (
            patch(f"{_AUTH_MOD}.capture_loopback_redirect", fake_capture),
            patch(f"{_AUTH_MOD}.exchange_code", exchange),
        ):
            with pytest.raises(RuntimeError, match="state"):
                await run_browser_auth(mock_storage, _UID)

        exchange.assert_not_awaited()
        mock_storage.save_token.assert_not_awaited()

    async def test_missing_code_rejected(
        self, tidal_creds, mock_storage: AsyncMock
    ) -> None:
        def fake_capture(
            auth_url: str, port: int, *, service_label: str
        ) -> dict[str, str]:
            return {"code": "", "state": ""}

        with patch(f"{_AUTH_MOD}.capture_loopback_redirect", fake_capture):
            with pytest.raises(RuntimeError, match="code"):
                await run_browser_auth(mock_storage, _UID)

        mock_storage.save_token.assert_not_awaited()


class TestRedirectPort:
    """Pure helper: local listen port from the configured redirect URI."""

    def test_explicit_port_wins(self) -> None:
        assert _redirect_port("http://127.0.0.1:8899/callback") == 8899

    def test_http_defaults_to_80(self) -> None:
        assert _redirect_port("http://localhost/callback") == 80

    def test_unparseable_uri_is_an_error(self) -> None:
        with pytest.raises(RuntimeError):
            _ = _redirect_port("")
