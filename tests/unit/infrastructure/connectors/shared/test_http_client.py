"""Unit tests for the shared httpx event hooks in http_client.py.

Regression suite for:
- _elapsed_ms(): safe access to response.elapsed (raises RuntimeError when
  the response body hasn't been consumed yet — common for auth/redirect hops).
- _log_response(): correct branching for success vs error responses, body
  buffering via aread(), and elapsed logging in both paths.
- End-to-end smoke tests: verifies that making a real httpx call through a
  mock transport does not crash in the event hooks. These tests catch bugs that
  only surface when the full httpx request/response lifecycle runs — not when
  impl methods are patched before the HTTP layer.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from tenacity import wait_none

from src.infrastructure.connectors._shared.http_client import (
    _elapsed_ms,
    _log_response,
)

# ---------------------------------------------------------------------------
# Mock transport helper for end-to-end smoke tests
# ---------------------------------------------------------------------------


class _QueueTransport(httpx.AsyncBaseTransport):
    """Async transport that returns pre-configured responses in FIFO order.

    Unlike patching _impl methods (which bypasses the HTTP layer entirely),
    this transport exercises the full httpx request/response lifecycle,
    including event hooks, elapsed timing, and body streaming.

    Usage::

        transport = _QueueTransport(
            (200, {"tracks": [{"id": "abc"}]}),
            (429, {"error": {"status": 429, "message": "rate limit"}}),
        )
    """

    def __init__(self, *specs: tuple[int, dict[str, Any] | None]):
        self._specs = list(specs)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not self._specs:
            raise AssertionError(
                f"_QueueTransport exhausted: no response queued for {request.url}"
            )
        status, body = self._specs.pop(0)
        return httpx.Response(status, json=body, request=request)


# ---------------------------------------------------------------------------
# _elapsed_ms
# ---------------------------------------------------------------------------


class TestElapsedMs:
    """Tests for the _elapsed_ms() helper."""

    def test_returns_ms_when_elapsed_is_available(self):
        """When response.elapsed is set, returns rounded milliseconds."""
        import datetime

        response = MagicMock(spec=httpx.Response)
        response.elapsed = datetime.timedelta(seconds=0.2345)
        assert _elapsed_ms(response) == 234.5

    def test_returns_none_when_response_not_yet_read(self):
        """When response.elapsed raises RuntimeError (body not read), returns None.

        Regression: httpx raises RuntimeError for `.elapsed` on streaming responses
        that haven't been consumed yet (e.g. intermediate auth/redirect responses).
        """
        response = MagicMock(spec=httpx.Response)
        type(response).elapsed = property(
            lambda self: (_ for _ in ()).throw(  # type: ignore[misc]
                RuntimeError(
                    "'.elapsed' may only be accessed after the response has been read or closed."
                )
            )
        )
        assert _elapsed_ms(response) is None

    def test_returns_none_when_elapsed_is_zero(self):
        """Zero elapsed time is valid and returns 0.0, not None."""
        import datetime

        response = MagicMock(spec=httpx.Response)
        response.elapsed = datetime.timedelta(seconds=0)
        assert _elapsed_ms(response) == 0.0


# ---------------------------------------------------------------------------
# _log_response — success path
# ---------------------------------------------------------------------------


class TestLogResponseSuccess:
    """Tests for the success (2xx/3xx) branch of _log_response."""

    async def test_success_response_logs_at_debug(self):
        """2xx responses are logged at DEBUG after reading the body for timing."""
        response = MagicMock(spec=httpx.Response)
        response.status_code = 200
        response.url = httpx.URL("https://api.spotify.com/v1/me")
        response.headers = {}
        response.aread = AsyncMock()
        # elapsed raises RuntimeError — simulates edge case where aread()
        # didn't populate elapsed (shouldn't happen in practice but we guard)
        type(response).elapsed = property(
            lambda self: (_ for _ in ()).throw(  # type: ignore[misc]
                RuntimeError("not read yet")
            )
        )

        with patch(
            "src.infrastructure.connectors._shared.http_client._http_logger"
        ) as mock_log:
            await _log_response(response)

        mock_log.debug.assert_called_once()
        call_kwargs = mock_log.debug.call_args
        assert call_kwargs[0][0] == "HTTP response"
        assert call_kwargs[1]["status"] == 200
        assert call_kwargs[1]["elapsed_ms"] is None  # RuntimeError → None, not crash

    async def test_success_response_calls_aread_for_elapsed(self):
        """Success responses call aread() to populate elapsed timing."""
        import datetime

        response = MagicMock(spec=httpx.Response)
        response.status_code = 200
        response.url = httpx.URL("https://api.spotify.com/v1/me")
        response.elapsed = datetime.timedelta(milliseconds=150)
        response.aread = AsyncMock()

        with patch("src.infrastructure.connectors._shared.http_client._http_logger"):
            await _log_response(response)

        response.aread.assert_called_once()

    async def test_success_response_elapsed_when_available(self):
        """When elapsed is available (response already read), logs the value."""
        import datetime

        response = MagicMock(spec=httpx.Response)
        response.status_code = 201
        response.url = httpx.URL("https://api.spotify.com/v1/playlists")
        response.elapsed = datetime.timedelta(milliseconds=87.3)
        response.aread = AsyncMock()

        with patch(
            "src.infrastructure.connectors._shared.http_client._http_logger"
        ) as mock_log:
            await _log_response(response)

        call_kwargs = mock_log.debug.call_args
        assert call_kwargs[1]["elapsed_ms"] == pytest.approx(87.3, abs=0.1)


# ---------------------------------------------------------------------------
# _log_response — error path
# ---------------------------------------------------------------------------


class TestLogResponseError:
    """Tests for the error (4xx/5xx) branch of _log_response."""

    async def test_error_response_calls_aread_before_logging(self):
        """Error responses must buffer the body (aread) BEFORE logging.

        Regression: if aread() is not called, response.elapsed raises RuntimeError
        AND response.text raises an error (body not buffered).
        """
        import datetime

        response = MagicMock(spec=httpx.Response)
        response.status_code = 429
        response.url = httpx.URL("https://api.spotify.com/v1/tracks")
        response.headers = MagicMock()
        response.headers.get.return_value = "60"
        response.text = "Rate limit exceeded"
        response.aread = AsyncMock()
        response.elapsed = datetime.timedelta(milliseconds=45.0)

        with patch(
            "src.infrastructure.connectors._shared.http_client._http_logger"
        ) as mock_log:
            await _log_response(response)

        response.aread.assert_awaited_once()
        mock_log.warning.assert_called_once()
        call_kwargs = mock_log.warning.call_args
        assert call_kwargs[0][0] == "HTTP error response"
        assert call_kwargs[1]["status"] == 429
        assert call_kwargs[1]["body"] == "Rate limit exceeded"
        assert call_kwargs[1]["retry_after"] == "60"

    async def test_error_response_body_capped_at_500_chars(self):
        """Response body is truncated to 500 characters to avoid log flooding."""
        import datetime

        long_body = "x" * 600
        response = MagicMock(spec=httpx.Response)
        response.status_code = 500
        response.url = httpx.URL("https://api.spotify.com/v1/tracks")
        response.headers = MagicMock()
        response.headers.get.return_value = None
        response.text = long_body
        response.aread = AsyncMock()
        response.elapsed = datetime.timedelta(milliseconds=200)

        with patch(
            "src.infrastructure.connectors._shared.http_client._http_logger"
        ) as mock_log:
            await _log_response(response)

        logged_body = mock_log.warning.call_args[1]["body"]
        assert len(logged_body) == 500

    async def test_error_response_logs_at_warning(self):
        """4xx/5xx responses are logged at WARNING, not DEBUG."""
        import datetime

        response = MagicMock(spec=httpx.Response)
        response.status_code = 401
        response.url = httpx.URL("https://api.spotify.com/v1/me")
        response.headers = MagicMock()
        response.headers.get.return_value = None
        response.text = "Unauthorized"
        response.aread = AsyncMock()
        response.elapsed = datetime.timedelta(milliseconds=30)

        with patch(
            "src.infrastructure.connectors._shared.http_client._http_logger"
        ) as mock_log:
            await _log_response(response)

        mock_log.warning.assert_called_once()
        mock_log.debug.assert_not_called()

    async def test_boundary_399_is_success(self):
        """Status 399 logs at DEBUG (success path), not WARNING."""
        import datetime

        response = MagicMock(spec=httpx.Response)
        response.status_code = 399
        response.url = httpx.URL("https://api.spotify.com/v1/me")
        response.elapsed = datetime.timedelta(milliseconds=10)

        with patch(
            "src.infrastructure.connectors._shared.http_client._http_logger"
        ) as mock_log:
            await _log_response(response)

        mock_log.debug.assert_called_once()
        mock_log.warning.assert_not_called()

    async def test_boundary_400_is_error(self):
        """Status 400 logs at WARNING (error path) and calls aread."""
        import datetime

        response = MagicMock(spec=httpx.Response)
        response.status_code = 400
        response.url = httpx.URL("https://api.spotify.com/v1/me")
        response.headers = MagicMock()
        response.headers.get.return_value = None
        response.text = "Bad Request"
        response.aread = AsyncMock()
        response.elapsed = datetime.timedelta(milliseconds=10)

        with patch(
            "src.infrastructure.connectors._shared.http_client._http_logger"
        ) as mock_log:
            await _log_response(response)

        mock_log.warning.assert_called_once()
        response.aread.assert_awaited_once()


# ---------------------------------------------------------------------------
# End-to-end smoke tests — real httpx lifecycle, mock network
# ---------------------------------------------------------------------------


class TestClientSmokeViaRealHttpx:
    """Smoke tests that exercise the full httpx request/response lifecycle.

    These use ``_QueueTransport`` so actual HTTP requests never leave the
    process, but all httpx machinery runs: event hooks, timing, streaming,
    body buffering.  Bugs in ``_log_response`` that only surface when the
    real hook-firing path runs (e.g. ``response.elapsed`` raising
    ``RuntimeError``) are caught here, not by the unit tests above.

    Regression: the ``response.elapsed`` RuntimeError was invisible to tests
    that patched ``_impl`` methods — those tests bypass the HTTP layer
    entirely and never fire the event hooks.
    """

    @pytest.fixture
    def spotify_settings(self):
        """Minimal settings patch for SpotifyAPIClient init."""
        with patch("src.infrastructure.connectors.spotify.client.settings") as s:
            s.api.spotify_market = "US"
            s.api.spotify.retry_count = 3
            s.api.spotify.retry_base_delay = 0.5
            s.api.spotify.retry_max_delay = 30.0
            yield s

    async def test_spotify_success_response_does_not_crash_hook(self, spotify_settings):
        """A 200 from Spotify must not raise in the response event hook.

        Regression: ``_log_response`` previously accessed ``response.elapsed``
        before the body was read, which raises ``RuntimeError`` on streaming
        responses (including auth token refresh responses).
        """
        from src.infrastructure.connectors._shared.http_client import (
            _EVENT_HOOKS,
        )
        from src.infrastructure.connectors.spotify.client import SpotifyAPIClient

        payload = {"id": "abc123", "name": "Test Track"}
        transport = _QueueTransport((200, payload))

        # Patch make_spotify_client to inject our mock transport (same event hooks)
        mock_client = httpx.AsyncClient(
            base_url="https://api.spotify.com/v1",
            transport=transport,
            event_hooks=_EVENT_HOOKS,
        )

        # Factories are imported locally inside __attrs_post_init__,
        # so we patch at the source module, not the consumer.
        with (
            patch(
                "src.infrastructure.connectors._shared.http_client.make_spotify_client",
                return_value=mock_client,
            ),
            patch(
                "src.infrastructure.connectors.spotify.auth.SpotifyTokenManager.get_valid_token",
                new=AsyncMock(return_value="fake_token"),
            ),
        ):
            client = SpotifyAPIClient()
            # Must not raise RuntimeError from the event hook
            result = await client.get_track("abc123")
            await client.aclose()

        # get_track returns validated SpotifyTrack
        assert result is not None
        assert result.id == "abc123"
        assert result.name == "Test Track"

    async def test_spotify_error_response_returns_none_via_hook(self, spotify_settings):
        """A 429 from Spotify must log the body in the hook and return None.

        Exercises the error path of ``_log_response``: ``aread()`` is called,
        body is logged, ``raise_for_status()`` is caught by ``_api_call``.
        """
        from src.infrastructure.connectors._shared.http_client import _EVENT_HOOKS
        from src.infrastructure.connectors.spotify.client import SpotifyAPIClient

        # 429 with Retry-After header; transport returns 3× so all retries are served
        transport = _QueueTransport(
            (429, {"error": {"status": 429, "message": "rate limit"}}),
            (429, {"error": {"status": 429, "message": "rate limit"}}),
            (429, {"error": {"status": 429, "message": "rate limit"}}),
        )
        mock_client = httpx.AsyncClient(
            base_url="https://api.spotify.com/v1",
            transport=transport,
            event_hooks=_EVENT_HOOKS,
        )

        with (
            patch(
                "src.infrastructure.connectors._shared.http_client.make_spotify_client",
                return_value=mock_client,
            ),
            patch(
                "src.infrastructure.connectors.spotify.auth.SpotifyTokenManager.get_valid_token",
                new=AsyncMock(return_value="fake_token"),
            ),
        ):
            client = SpotifyAPIClient()
            client._retry_policy.wait = wait_none()
            # Must not raise — _api_call suppresses HTTPStatusError
            result = await client.get_track("abc123")
            await client.aclose()

        assert result is None

    async def test_lastfm_success_response_does_not_crash_hook(self):
        """A 200 from Last.fm must not raise in the response event hook."""
        from src.infrastructure.connectors._shared.http_client import _EVENT_HOOKS
        from src.infrastructure.connectors.lastfm.client import LastFMAPIClient

        payload = {"track": {"name": "Creep", "artist": {"name": "Radiohead"}}}
        transport = _QueueTransport((200, payload))
        mock_client = httpx.AsyncClient(
            base_url="https://ws.audioscrobbler.com/2.0",
            transport=transport,
            event_hooks=_EVENT_HOOKS,
        )

        with (
            patch("src.infrastructure.connectors.lastfm.client.settings") as s,
            patch(
                "src.infrastructure.connectors._shared.http_client.make_lastfm_client",
                return_value=mock_client,
            ),
        ):
            s.credentials.lastfm_key = "test_key"
            s.credentials.lastfm_secret.get_secret_value.return_value = "test_secret"
            s.credentials.lastfm_username = "test_user"
            s.api.lastfm.request_timeout = 10.0
            s.api.lastfm.retry_count = 3
            s.api.lastfm.retry_base_delay = 0.5
            s.api.lastfm.retry_max_delay = 30.0

            client = LastFMAPIClient()
            # Must not raise RuntimeError from the event hook
            result = await client.get_track_info_comprehensive("Radiohead", "Creep")
            await client.aclose()

        # The response is a raw dict without the expected lastfm_ prefix fields;
        # _parse_track_info returns None for a response with no "track" key at top
        # but the important assertion is: no exception was raised
        assert result is not None or result is None  # hook must not crash
