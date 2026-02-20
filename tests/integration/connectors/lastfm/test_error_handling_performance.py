"""Performance tests for LastFM error handling to ensure error classification doesn't impact speed.

These tests verify that the enhanced error handling with classification doesn't
introduce performance regressions compared to basic error handling.
"""

import asyncio
from itertools import starmap
import time
from unittest.mock import AsyncMock, patch

import pytest

from src.infrastructure.connectors.lastfm.client import LastFMAPIClient
from src.infrastructure.connectors.lastfm.models import LastFMAPIError

# Minimal valid track.getInfo JSON response
_TRACK_DATA = {
    "track": {"name": "Fast Track", "artist": {"name": "Fast Artist"}, "playcount": "1000"}
}


@pytest.mark.integration
@pytest.mark.performance
class TestLastFMErrorHandlingPerformance:
    """Performance tests for error handling in LastFM comprehensive API methods."""

    @pytest.fixture
    def lastfm_client(self):
        """LastFM client with proper configuration."""
        with patch(
            "src.infrastructure.connectors.lastfm.client.settings"
        ) as mock_settings:
            mock_settings.credentials.lastfm_key = "test_key"
            mock_settings.credentials.lastfm_secret.get_secret_value.return_value = (
                "test_secret"
            )
            mock_settings.credentials.lastfm_username = "test_user"
            mock_settings.api.lastfm_rate_limit = 100.0
            mock_settings.api.lastfm_concurrency = 50
            mock_settings.api.lastfm_request_timeout = 10.0
            yield LastFMAPIClient()

    @pytest.mark.asyncio
    async def test_successful_calls_performance_baseline(self, lastfm_client):
        """Baseline performance test for successful comprehensive API calls."""
        mock_api = AsyncMock(return_value=_TRACK_DATA)

        with patch.object(LastFMAPIClient, "_api_request", mock_api):
            num_calls = 20
            start_time = time.time()

            tasks = [
                lastfm_client.get_track_info_comprehensive(f"Artist{i}", f"Track{i}")
                for i in range(num_calls)
            ]
            results = await asyncio.gather(*tasks)

            duration = time.time() - start_time

        assert len(results) == num_calls
        assert all(r is not None for r in results)

        assert duration < 2.0, (
            f"Successful calls took {duration:.2f}s, expected < 2.0s"
        )

        calls_per_second = num_calls / duration
        assert calls_per_second > 10, (
            f"Only {calls_per_second:.1f} calls/sec, expected > 10"
        )

        print(
            f"✅ Successful calls: {num_calls} calls in {duration:.2f}s "
            f"({calls_per_second:.1f} calls/sec)"
        )

    @pytest.mark.asyncio
    async def test_not_found_errors_performance(self, lastfm_client):
        """Test that not_found errors are handled quickly (no retries)."""
        # Error code 999 + "not found" text → classified as not_found → no retry
        mock_api = AsyncMock(side_effect=LastFMAPIError("999", "Track not found"))

        with patch.object(LastFMAPIClient, "_api_request", mock_api):
            num_calls = 20
            start_time = time.time()

            tasks = [
                lastfm_client.get_track_info_comprehensive(f"Missing{i}", f"Track{i}")
                for i in range(num_calls)
            ]
            results = await asyncio.gather(*tasks)

            duration = time.time() - start_time

        assert len(results) == num_calls
        assert all(r is None for r in results)

        # Should be fast since no retries
        assert duration < 1.0, (
            f"Not found errors took {duration:.2f}s, expected < 1.0s"
        )

        # Verify only one call per track (no retries)
        assert mock_api.call_count == num_calls

        print(
            f"✅ Not found errors: {num_calls} calls in {duration:.2f}s (no retries)"
        )

    @pytest.mark.asyncio
    async def test_permanent_errors_performance(self, lastfm_client):
        """Test that permanent errors are handled quickly (no retries)."""
        # Error code 10 (Invalid API key) → classified as permanent → no retry
        mock_api = AsyncMock(side_effect=LastFMAPIError("10", "Invalid API key"))

        with patch.object(LastFMAPIClient, "_api_request", mock_api):
            num_calls = 10
            start_time = time.time()

            tasks = [
                lastfm_client.get_track_info_comprehensive(f"Artist{i}", f"Track{i}")
                for i in range(num_calls)
            ]
            results = await asyncio.gather(*tasks)

            duration = time.time() - start_time

        assert len(results) == num_calls
        assert all(r is None for r in results)

        # Should be fast since no retries for permanent errors
        assert duration < 0.5, (
            f"Permanent errors took {duration:.2f}s, expected < 0.5s"
        )

        assert mock_api.call_count == num_calls

        print(
            f"✅ Permanent errors: {num_calls} calls in {duration:.2f}s (no retries)"
        )

    @pytest.mark.asyncio
    async def test_mixed_scenario_performance(self, lastfm_client):
        """Test performance with mixed success/error scenarios."""

        async def mock_api_route(method, params=None, authenticated=False):
            track = (params or {}).get("track", "").lower()
            if "success" in track or "default" in track:
                return _TRACK_DATA
            elif "notfound" in track:
                raise LastFMAPIError("999", "track not found")
            elif "permanent" in track:
                raise LastFMAPIError("10", "Invalid API key")
            else:
                return _TRACK_DATA

        with patch.object(LastFMAPIClient, "_api_request", side_effect=mock_api_route):
            test_scenarios = [
                ("Artist1", "Success Track 1"),
                ("Artist2", "NotFound Track"),
                ("Artist3", "Success Track 2"),
                ("Artist4", "Permanent Error"),
                ("Artist5", "Success Track 3"),
                ("Artist6", "NotFound Again"),
                ("Artist7", "Success Track 4"),
            ]

            start_time = time.time()
            tasks = list(starmap(lastfm_client.get_track_info_comprehensive, test_scenarios))
            results = await asyncio.gather(*tasks)
            duration = time.time() - start_time

        successful_results = [r for r in results if r is not None]
        failed_results = [r for r in results if r is None]

        # 4 success tracks, 3 failure tracks (2 not_found + 1 permanent)
        assert len(successful_results) == 4
        assert len(failed_results) == 3

        # Should be reasonably fast (no retries for errors)
        assert duration < 1.5, (
            f"Mixed scenario took {duration:.2f}s, expected < 1.5s"
        )

        success_rate = len(successful_results) / len(results) * 100
        print(
            f"✅ Mixed scenario: {len(results)} calls in {duration:.2f}s "
            f"({success_rate:.1f}% success rate)"
        )

    @pytest.mark.asyncio
    async def test_error_classification_overhead(self, lastfm_client):
        """Test that error classification doesn't add significant overhead."""
        mock_api = AsyncMock(side_effect=LastFMAPIError("999", "not found"))

        with patch.object(LastFMAPIClient, "_api_request", mock_api):
            num_calls = 50
            start_time = time.time()

            results = []
            for i in range(num_calls):
                result = await lastfm_client.get_track_info_comprehensive(
                    f"Artist{i}", f"Track{i}"
                )
                results.append(result)

            duration = time.time() - start_time

        assert all(r is None for r in results)

        overhead_per_call = duration / num_calls

        assert overhead_per_call < 0.01, (
            f"Error classification overhead {overhead_per_call:.3f}s per call, expected < 0.01s"
        )

        print(
            f"✅ Error classification overhead: {overhead_per_call:.3f}s per call "
            f"for {num_calls} calls"
        )
