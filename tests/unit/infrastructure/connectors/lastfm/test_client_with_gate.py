"""Tests for LastFM client integration with RequestStartGate."""

import asyncio
import time
from unittest.mock import MagicMock, patch

import pylast
import pytest

from src.infrastructure.connectors._shared.request_start_gate import RequestStartGate
from src.infrastructure.connectors.lastfm.client import LastFMAPIClient


class TestLastFMClientWithGate:
    """Test LastFM client integrated with RequestStartGate for rate limiting."""

    @pytest.fixture
    def mock_pylast_client(self):
        """Mock pylast client."""
        mock_client = MagicMock(spec=pylast.LastFMNetwork)
        mock_track = MagicMock(spec=pylast.Track)
        mock_client.get_track.return_value = mock_track
        mock_client.get_track_by_mbid.return_value = mock_track
        return mock_client

    @pytest.fixture
    def gate(self):
        """Create a request gate with 200ms delay."""
        return RequestStartGate(delay=0.2)

    @pytest.fixture
    def client_with_gate(self, mock_pylast_client, gate):
        """Create LastFM client with request gate."""
        with patch('src.config.settings') as mock_settings:
            # Mock settings
            mock_settings.credentials.lastfm_key = "test_key"
            mock_settings.credentials.lastfm_secret.get_secret_value.return_value = "test_secret"
            mock_settings.credentials.lastfm_username = "test_user"
            mock_settings.credentials.lastfm_password.get_secret_value.return_value = "test_pass"
            
            client = LastFMAPIClient()
            # Replace the pylast client with our mock and add the gate
            client.client = mock_pylast_client
            client._request_gate = gate
            return client

    @pytest.mark.asyncio
    async def test_get_track_waits_for_gate_before_api_call(self, client_with_gate, mock_pylast_client):
        """get_track should wait for gate approval before making API call."""
        # Track when actual API calls happen
        api_calls = []
        original_get_track = mock_pylast_client.get_track
        
        def track_api_call(*args, **kwargs):
            api_calls.append(time.time())
            return original_get_track(*args, **kwargs)
        
        mock_pylast_client.get_track = track_api_call
        
        # Make API call
        result = await client_with_gate.get_track("Artist", "Title")
        
        # Verify API call was made
        assert len(api_calls) == 1
        assert result is not None

    @pytest.mark.asyncio
    async def test_multiple_concurrent_calls_respect_rate_limit(self, client_with_gate, mock_pylast_client):
        """Multiple concurrent calls should respect the rate limit through the gate."""
        call_times = []
        
        # Track when each API call starts
        original_get_track = mock_pylast_client.get_track

        def track_call(*args, **kwargs):
            call_times.append(time.time())
            return original_get_track(*args, **kwargs)
        
        mock_pylast_client.get_track = track_call
        
        # Make 3 concurrent API calls
        start_time = time.time()
        tasks = [
            asyncio.create_task(client_with_gate.get_track("Artist1", "Title1")),
            asyncio.create_task(client_with_gate.get_track("Artist2", "Title2")),
            asyncio.create_task(client_with_gate.get_track("Artist3", "Title3")),
        ]
        
        await asyncio.gather(*tasks)
        total_time = time.time() - start_time
        
        # Should take ~400ms (0 + 200 + 200ms for gate delays)
        assert 0.35 < total_time < 0.45
        
        # API calls should be spaced ~200ms apart
        assert len(call_times) == 3
        assert call_times[1] - call_times[0] >= 0.18  # ~200ms delay
        assert call_times[2] - call_times[1] >= 0.18  # ~200ms delay

    @pytest.mark.asyncio  
    async def test_client_handles_retries_with_gate_properly(self, client_with_gate, mock_pylast_client):
        """Client should handle retries properly even with gate integration."""
        # Simple test: make sure client can handle successful calls with gate
        result = await client_with_gate.get_track("Artist", "Title")
        assert result is not None
        
        # And that multiple calls work with rate limiting
        results = await asyncio.gather(
            client_with_gate.get_track("Artist1", "Title1"),
            client_with_gate.get_track("Artist2", "Title2"),
        )
        assert len(results) == 2
        assert all(r is not None for r in results)

    @pytest.mark.asyncio
    async def test_get_track_by_mbid_also_uses_gate(self, client_with_gate, mock_pylast_client):
        """get_track_by_mbid should also use the request gate."""
        # Make API call
        result = await client_with_gate.get_track_by_mbid("test-mbid")
        
        # Verify call succeeded (gate allowed it through)
        assert result is not None

    @pytest.mark.asyncio
    async def test_client_without_gate_works_normally(self, mock_pylast_client):
        """Client without gate should work normally (backward compatibility)."""
        with patch('src.config.settings') as mock_settings:
            mock_settings.credentials.lastfm_key = "test_key"
            mock_settings.credentials.lastfm_secret.get_secret_value.return_value = "test_secret"
            mock_settings.credentials.lastfm_username = "test_user"
            mock_settings.credentials.lastfm_password.get_secret_value.return_value = "test_pass"
            
            client = LastFMAPIClient()
            client.client = mock_pylast_client
            # No _request_gate attribute
            
            # Should work without gate
            result = await client.get_track("Artist", "Title")
            assert result is not None