"""Integration tests for LastFM error handling with actual client behavior.

These tests focus on integration-level behavior:
- Real client initialization and configuration
- Actual backoff decorator behavior
- End-to-end error handling flow
- Mock only the network layer (pylast.LastFMNetwork)

Keep tests lean, fast, and focused on integration behavior.
"""

import time
from unittest.mock import MagicMock, patch

import pylast
import pytest

from src.infrastructure.connectors.lastfm.client import LastFMAPIClient


@pytest.mark.integration
class TestErrorHandlingIntegration:
    """Integration tests for error handling - test actual client behavior with mocked network."""

    @pytest.fixture
    def lastfm_client(self):
        """LastFM client with mocked settings."""
        with patch("src.config.settings") as mock_settings:
            mock_settings.api.lastfm_retry_count_rate_limit = 8
            mock_settings.api.lastfm_retry_count_network = 5
            mock_settings.api.lastfm_retry_max_delay = 60.0
            mock_settings.api.lastfm_request_timeout = 10.0
            mock_settings.credentials.lastfm_key = "test_key"
            mock_settings.credentials.lastfm_secret.get_secret_value.return_value = (
                "test_secret"
            )
            mock_settings.credentials.lastfm_username = "test_user"
            mock_settings.credentials.lastfm_password.get_secret_value.return_value = (
                "test_pass"
            )
            yield LastFMAPIClient()

    @pytest.mark.asyncio
    async def test_permanent_error_no_retries(self, lastfm_client):
        """Test permanent errors stop immediately with no retries."""
        call_count = 0

        def mock_permanent_error(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise pylast.WSError("LastFm", "10", "Invalid API key")

        with patch("pylast.LastFMNetwork") as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_permanent_error
            mock_network_class.return_value = mock_network

            start_time = time.time()
            result = await lastfm_client.get_track_info_comprehensive("Artist", "Track")
            duration = time.time() - start_time

            # Should return None (graceful failure)
            assert result is None

            # Should NOT retry - permanent errors fail immediately
            assert call_count == 1

            # Should be fast (no retry delays)
            assert duration < 1.0

    @pytest.mark.asyncio
    async def test_rate_limit_error_classified_correctly(self, lastfm_client):
        """Test rate limit errors are classified correctly and do trigger retries."""
        call_count = 0

        def mock_rate_limit_error(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:  # Succeed after 2 retries to avoid long test times
                raise pylast.WSError("LastFm", "29", "Rate Limit Exceeded")
            # Return success on 3rd try
            mock_track = MagicMock(spec=pylast.Track)
            mock_track._request.return_value = MagicMock()
            return mock_track

        with patch("pylast.LastFMNetwork") as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_rate_limit_error
            mock_network_class.return_value = mock_network

            # Mock comprehensive data extraction
            with patch(
                "src.infrastructure.connectors.lastfm.client.LastFMAPIClient._get_comprehensive_track_data",
                return_value={"test": "rate_limit_success"},
            ):
                result = await lastfm_client.get_track_info_comprehensive(
                    "Artist", "Track"
                )

                # Should succeed after retries
                assert result is not None
                assert result == {"test": "rate_limit_success"}

                # Should have made 3 calls (2 failures + 1 success)
                assert call_count == 3

    @pytest.mark.asyncio
    async def test_network_error_classified_correctly(self, lastfm_client):
        """Test network/temporary errors are classified correctly and do trigger retries."""
        call_count = 0

        def mock_network_error(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:  # Succeed after 2 retries to avoid long test times
                raise pylast.WSError("LastFm", "11", "Service Offline")
            # Return success on 3rd try
            mock_track = MagicMock(spec=pylast.Track)
            mock_track._request.return_value = MagicMock()
            return mock_track

        with patch("pylast.LastFMNetwork") as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_network_error
            mock_network_class.return_value = mock_network

            # Mock comprehensive data extraction
            with patch(
                "src.infrastructure.connectors.lastfm.client.LastFMAPIClient._get_comprehensive_track_data",
                return_value={"test": "network_success"},
            ):
                result = await lastfm_client.get_track_info_comprehensive(
                    "Artist", "Track"
                )

                # Should succeed after retries
                assert result is not None
                assert result == {"test": "network_success"}

                # Should have made 3 calls (2 failures + 1 success)
                assert call_count == 3

    @pytest.mark.asyncio
    async def test_not_found_error_no_retries(self, lastfm_client):
        """Test not found errors stop immediately with no retries."""
        call_count = 0

        def mock_not_found_error(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise pylast.WSError("LastFm", "999", "Track not found")

        with patch("pylast.LastFMNetwork") as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_not_found_error
            mock_network_class.return_value = mock_network

            start_time = time.time()
            result = await lastfm_client.get_track_info_comprehensive("Artist", "Track")
            duration = time.time() - start_time

            # Should return None (graceful failure)
            assert result is None

            # Should NOT retry - not found errors fail immediately
            assert call_count == 1

            # Should be fast (no retry delays)
            assert duration < 1.0

    @pytest.mark.asyncio
    async def test_success_after_retries(self, lastfm_client):
        """Test that requests succeed after initial failures."""
        call_count = 0

        def mock_success_after_retries(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            if call_count <= 2:
                # Fail first 2 times with rate limit
                raise pylast.WSError("LastFm", "29", "Rate Limit Exceeded")

            # Succeed on 3rd attempt
            mock_track = MagicMock(spec=pylast.Track)
            mock_track._request.return_value = MagicMock()
            return mock_track

        with patch("pylast.LastFMNetwork") as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_success_after_retries
            mock_network_class.return_value = mock_network

            # Mock the comprehensive data extraction to return success
            with patch(
                "src.infrastructure.connectors.lastfm.client.LastFMAPIClient._get_comprehensive_track_data",
                return_value={"test": "success"},
            ):
                result = await lastfm_client.get_track_info_comprehensive(
                    "Artist", "Track"
                )

                # Should succeed after retries
                assert result is not None
                assert result == {"test": "success"}

                # Should have made 3 calls (2 failures + 1 success)
                assert call_count == 3

    @pytest.mark.asyncio
    async def test_error_type_behavior_isolation(self, lastfm_client):
        """Test that different error types behave independently and correctly."""

        # Test 1: Permanent error - should not retry
        perm_calls = 0

        def mock_permanent(*args, **kwargs):
            nonlocal perm_calls
            perm_calls += 1
            raise pylast.WSError("LastFm", "10", "Invalid API key")

        with patch("pylast.LastFMNetwork") as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_permanent
            mock_network_class.return_value = mock_network

            perm_result = await lastfm_client.get_track_info_comprehensive(
                "Permanent", "Error"
            )

        # Test 2: Rate limit error - should retry
        rate_calls = 0

        def mock_rate_limit(*args, **kwargs):
            nonlocal rate_calls
            rate_calls += 1
            if rate_calls < 2:  # Quick success to avoid timeouts
                raise pylast.WSError("LastFm", "29", "Rate Limit Exceeded")
            mock_track = MagicMock(spec=pylast.Track)
            mock_track._request.return_value = MagicMock()
            return mock_track

        with patch("pylast.LastFMNetwork") as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_rate_limit
            mock_network_class.return_value = mock_network

            with patch(
                "src.infrastructure.connectors.lastfm.client.LastFMAPIClient._get_comprehensive_track_data",
                return_value={"test": "success"},
            ):
                rate_result = await lastfm_client.get_track_info_comprehensive(
                    "RateLimit", "Error"
                )

        # Verify behavior
        assert perm_result is None  # Permanent error returns None
        assert perm_calls == 1  # No retries for permanent errors

        assert rate_result is not None  # Rate limit eventually succeeds
        assert rate_calls == 2  # Retried once then succeeded

    @pytest.mark.asyncio
    async def test_edge_case_error_code_6_with_rate_limit_text(self, lastfm_client):
        """Test critical edge case: error code 6 + rate limit text = permanent (code precedence)."""
        call_count = 0

        def mock_edge_case_error(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # Error code 6 (permanent) but with "rate limit" in message
            raise pylast.WSError(
                "LastFm", "6", "Invalid parameters - rate limit exceeded"
            )

        with patch("pylast.LastFMNetwork") as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_edge_case_error
            mock_network_class.return_value = mock_network

            start_time = time.time()
            result = await lastfm_client.get_track_info_comprehensive("Artist", "Track")
            duration = time.time() - start_time

            # Should return None (graceful failure)
            assert result is None

            # Should NOT retry - error CODE takes precedence over text patterns
            assert call_count == 1

            # Should be fast (no retry delays)
            assert duration < 1.0
