"""Integration tests for LastFM retry behavior with real tenacity policy.

Tests verify the interaction between the error classifier, retry predicate,
and the tenacity retry policy in the LastFMAPIClient. Classification logic
itself is tested in tests/unit/infrastructure/connectors/lastfm/test_error_classifier.py.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.infrastructure.connectors.lastfm.client import LastFMAPIClient
from src.infrastructure.connectors.lastfm.models import LastFMAPIError

_MINIMAL_TRACK_DATA = {
    "track": {"name": "Test Track", "artist": {"name": "Test Artist"}}
}


@pytest.mark.slow
class TestLastFMRetryBehavior:
    """Tests for LastFM client retry policy integration."""

    @pytest.fixture
    def lastfm_client(self):
        """LastFM client with mocked settings."""
        with patch(
            "src.infrastructure.connectors.lastfm.client.settings"
        ) as mock_settings:
            mock_settings.credentials.lastfm_key = "test_key"
            mock_settings.credentials.lastfm_secret.get_secret_value.return_value = (
                "test_secret"
            )
            mock_settings.credentials.lastfm_username = "test_user"
            mock_settings.api.lastfm.rate_limit = 10.0
            mock_settings.api.lastfm.concurrency = 50
            mock_settings.api.lastfm.request_timeout = 10.0
            mock_settings.api.lastfm.retry_count = 8
            mock_settings.api.lastfm.retry_base_delay = 1.0
            mock_settings.api.lastfm.retry_max_delay = 60.0
            yield LastFMAPIClient()

    @pytest.fixture
    def fast_retry_client(self, lastfm_client):
        """Client with instant retries — no exponential backoff waits."""
        from tenacity import wait_none

        lastfm_client._retry_policy.wait = wait_none()
        return lastfm_client

    async def test_non_lastfm_exception_propagates(self, lastfm_client):
        """Non-LastFMAPIError exceptions propagate immediately without retries.

        ValueError from _api_request is a programming error. The retry policy
        only retries LastFMAPIError/httpx2 exceptions; others propagate to caller.
        """
        mock_api = AsyncMock(
            side_effect=ValueError("Programming error - not an API error")
        )

        with patch.object(LastFMAPIClient, "_api_request", mock_api):
            with pytest.raises(ValueError, match="Programming error"):
                await lastfm_client.get_track_info_comprehensive(
                    "Test Artist", "Test Track"
                )

        assert mock_api.call_count == 1

    async def test_maximum_retry_exhaustion(self, fast_retry_client):
        """Temporary errors exhaust all retries then RAISE (v0.10.2.9 F5).

        The enrichment read passes ``suppress=()``: a retry-exhausted outage
        must stay visible to the caller, never collapse into the ``None``
        that means "track not found" — that collapse is what let an outage
        mint permanent junk canonicals.
        """
        mock_api = AsyncMock(
            side_effect=LastFMAPIError("11", "Service Offline - Always fails")
        )

        with patch.object(LastFMAPIClient, "_api_request", mock_api):
            with pytest.raises(LastFMAPIError, match="Service Offline"):
                await fast_retry_client.get_track_info_comprehensive(
                    "Test Artist", "Test Track"
                )

        # retry_count=8 in the fixture: every attempt is spent before raising.
        assert mock_api.call_count == 8
