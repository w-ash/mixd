"""Integration tests for LastFM error classification and retry behavior.

This test suite verifies that LastFM API client methods properly handle different
error types according to the error classification rules:

- Rate limit errors → Retry with exponential backoff
- Temporary errors → Retry with exponential backoff  
- Permanent errors → No retry, immediate failure
- Not found errors → No retry, immediate failure

Tests focus on core functionality without over-complex mocking or scenarios.
"""

import time
from unittest.mock import MagicMock, patch

import pylast
import pytest

from src.infrastructure.connectors.lastfm.client import LastFMAPIClient


@pytest.mark.integration
class TestLastFMErrorClassificationIntegration:
    """Integration tests for error classification with API methods."""

    @pytest.fixture
    def lastfm_client(self):
        """LastFM client with mocked settings."""
        with patch('src.config.settings') as mock_settings:
            mock_settings.credentials.lastfm_key = "test_key"
            mock_settings.credentials.lastfm_secret.get_secret_value.return_value = "test_secret"
            mock_settings.credentials.lastfm_username = "test_user"
            mock_settings.credentials.lastfm_password.get_secret_value.return_value = "test_pass"
            mock_settings.api.lastfm_rate_limit = 10.0
            mock_settings.api.lastfm_concurrency = 50
            mock_settings.api.lastfm_request_timeout = 10.0
            yield LastFMAPIClient()

    @pytest.mark.asyncio
    async def test_not_found_error_no_retry(self, lastfm_client):
        """Test that 'not found' errors are handled gracefully without retry."""
        
        def mock_get_track_not_found(*args, **kwargs):
            """Mock that raises a 'not found' error."""
            raise pylast.WSError("LastFm", "999", "Track not found")
        
        with patch('pylast.LastFMNetwork') as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_get_track_not_found
            mock_network_class.return_value = mock_network
            
            start_time = time.time()
            result = await lastfm_client.get_track_info_comprehensive("Unknown Artist", "Unknown Track")
            duration = time.time() - start_time
            
            # Should return None gracefully, not raise exception
            assert result is None
            
            # Should only try once (no retries for not_found)
            assert mock_network.get_track.call_count == 1
            
            # Should be fast (no retry delays)
            assert duration < 0.5

    @pytest.mark.asyncio
    async def test_permanent_error_no_retry(self, lastfm_client):
        """Test that permanent errors don't retry and return None gracefully."""
        
        def mock_get_track_permanent_error(*args, **kwargs):
            """Mock that raises a permanent error (invalid API key)."""
            raise pylast.WSError("LastFm", "10", "Invalid API key - You must be granted a valid key by last.fm")
        
        with patch('pylast.LastFMNetwork') as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_get_track_permanent_error
            mock_network_class.return_value = mock_network
            
            start_time = time.time()
            result = await lastfm_client.get_track_info_comprehensive("Test Artist", "Test Track")
            duration = time.time() - start_time
            
            # Should return None gracefully
            assert result is None
            
            # Should NOT retry (only 1 call)
            assert mock_network.get_track.call_count == 1
            
            # Should be fast (no retry delays)
            assert duration < 0.5

    @pytest.mark.asyncio
    async def test_rate_limit_error_retry_behavior(self, lastfm_client):
        """Test that rate limit errors trigger proper retry behavior."""
        
        call_count = 0
        
        def mock_get_track_rate_limited(*args, **kwargs):
            """Mock that raises rate limit error first, then succeeds."""
            nonlocal call_count
            call_count += 1
            
            if call_count <= 1:
                raise pylast.WSError("LastFm", "29", "Rate Limit Exceeded - Your IP has made too many requests")
            
            # Success on 2nd try - return properly mocked track
            mock_track = MagicMock(spec=pylast.Track)
            return mock_track
        
        # Mock the comprehensive data extraction to return consistent data
        mock_comprehensive_data = {
            'lastfm_title': 'Test Track',
            'lastfm_artist_name': 'Test Artist',
            'lastfm_global_playcount': 12345
        }
        
        with patch('pylast.LastFMNetwork') as mock_network_class, \
             patch.object(LastFMAPIClient, '_get_comprehensive_track_data', return_value=mock_comprehensive_data):
            
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_get_track_rate_limited
            mock_network_class.return_value = mock_network
            
            start_time = time.time()
            result = await lastfm_client.get_track_info_comprehensive("Test Artist", "Test Track")
            duration = time.time() - start_time
            
            # Should succeed after retries
            assert result == mock_comprehensive_data
            assert result['lastfm_title'] == 'Test Track'
            assert result['lastfm_artist_name'] == 'Test Artist'
            
            # Should have retried (2 calls total: 1 failure + 1 success)
            assert call_count == 2
            
            # Should have taken some time due to backoff delays
            assert duration > 0.1

    @pytest.mark.asyncio
    async def test_temporary_error_retry_behavior(self, lastfm_client):
        """Test that temporary errors trigger appropriate retries."""
        
        call_count = 0
        
        def mock_get_track_temporary_error(*args, **kwargs):
            """Mock that raises temporary error, then succeeds."""
            nonlocal call_count
            call_count += 1
            
            if call_count <= 1:
                raise pylast.WSError("LastFm", "11", "Service Offline - This service is temporarily offline. Try again later")
            
            # Success on 2nd try
            mock_track = MagicMock(spec=pylast.Track)
            return mock_track
        
        mock_comprehensive_data = {
            'lastfm_title': 'Temporary Track',
            'lastfm_artist_name': 'Temporary Artist',
            'lastfm_global_playcount': 54321
        }
        
        with patch('pylast.LastFMNetwork') as mock_network_class, \
             patch.object(LastFMAPIClient, '_get_comprehensive_track_data', return_value=mock_comprehensive_data):
            
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_get_track_temporary_error
            mock_network_class.return_value = mock_network
            
            start_time = time.time()
            result = await lastfm_client.get_track_info_comprehensive("Test Artist", "Test Track")
            duration = time.time() - start_time
            
            # Should succeed after retry
            assert result == mock_comprehensive_data
            assert result['lastfm_title'] == 'Temporary Track'
            assert result['lastfm_artist_name'] == 'Temporary Artist'
            
            # Should have retried (2 calls total: 1 failure + 1 success)
            assert call_count == 2
            
            # Should have some delay from retry
            assert duration > 0.05

    @pytest.mark.asyncio
    async def test_mbid_method_error_handling(self, lastfm_client):
        """Test error handling for MBID-based comprehensive method."""
        
        def mock_get_track_by_mbid_not_found(*args, **kwargs):
            """Mock MBID lookup that fails."""
            raise pylast.WSError("LastFm", "999", "Track with MBID not found")
        
        with patch('pylast.LastFMNetwork') as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track_by_mbid.side_effect = mock_get_track_by_mbid_not_found
            mock_network_class.return_value = mock_network
            
            # Test MBID comprehensive method
            result = await lastfm_client.get_track_info_comprehensive_by_mbid("fake-mbid-123")
            
            # Should return None gracefully
            assert result is None
            
            # Should only try once for not_found
            assert mock_network.get_track_by_mbid.call_count == 1

    @pytest.mark.asyncio
    async def test_love_track_error_handling(self, lastfm_client):
        """Test error handling for user library operations."""
        
        def mock_get_track_for_love(*args, **kwargs):
            """Mock track retrieval that succeeds."""
            mock_track = MagicMock()
            mock_track.love.side_effect = pylast.WSError("LastFm", "4", "Authentication Failed - You do not have permissions to access the service")
            return mock_track
        
        # Mock the client's get_track method directly since it uses self.client instance
        lastfm_client.client = MagicMock()
        lastfm_client.client.get_track.side_effect = mock_get_track_for_love
        
        # Test love track with auth error
        result = await lastfm_client.love_track("Test Artist", "Test Track")
        
        # Should return False gracefully for permanent authentication errors
        assert result is False


@pytest.mark.integration  
class TestLastFMHappyPathIntegration:
    """Integration tests for successful API call scenarios."""

    @pytest.fixture
    def lastfm_client(self):
        """LastFM client with mocked settings."""
        with patch('src.config.settings') as mock_settings:
            mock_settings.credentials.lastfm_key = "test_key"
            mock_settings.credentials.lastfm_secret.get_secret_value.return_value = "test_secret"
            mock_settings.credentials.lastfm_username = "test_user"
            mock_settings.credentials.lastfm_password.get_secret_value.return_value = "test_pass"
            mock_settings.api.lastfm_rate_limit = 10.0
            mock_settings.api.lastfm_concurrency = 50
            mock_settings.api.lastfm_request_timeout = 10.0
            yield LastFMAPIClient()

    @pytest.mark.asyncio
    async def test_comprehensive_api_success(self, lastfm_client):
        """Test successful comprehensive API call returns expected data."""
        
        def mock_successful_get_track(*args, **kwargs):
            """Mock successful track retrieval."""
            mock_track = MagicMock(spec=pylast.Track)
            return mock_track
        
        mock_comprehensive_data = {
            'lastfm_title': 'Test Track',
            'lastfm_artist_name': 'Test Artist',
            'lastfm_mbid': 'test-mbid-123',
            'lastfm_global_playcount': 12345,
            'lastfm_listeners': 6789,
            'lastfm_user_playcount': 42,
            'lastfm_user_loved': True
        }
        
        with patch('pylast.LastFMNetwork') as mock_network_class, \
             patch.object(LastFMAPIClient, '_get_comprehensive_track_data', return_value=mock_comprehensive_data):
            
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_successful_get_track
            mock_network_class.return_value = mock_network
            
            # Test comprehensive API method
            result = await lastfm_client.get_track_info_comprehensive("Test Artist", "Test Track")
            
            # Should return the comprehensive data
            assert result == mock_comprehensive_data
            assert result['lastfm_title'] == 'Test Track'
            assert result['lastfm_artist_name'] == 'Test Artist'
            assert result['lastfm_global_playcount'] == 12345
            assert result['lastfm_user_playcount'] == 42
            assert result['lastfm_user_loved'] is True

    @pytest.mark.asyncio
    async def test_mbid_comprehensive_api_success(self, lastfm_client):
        """Test successful MBID-based comprehensive API call."""
        
        def mock_successful_get_track_by_mbid(*args, **kwargs):
            """Mock successful track retrieval by MBID."""
            mock_track = MagicMock(spec=pylast.Track)
            return mock_track
        
        mock_mbid_data = {
            'lastfm_title': 'MBID Track',
            'lastfm_mbid': 'real-mbid-456',
            'lastfm_artist_name': 'MBID Artist',
            'lastfm_global_playcount': 98765
        }
        
        with patch('pylast.LastFMNetwork') as mock_network_class, \
             patch.object(LastFMAPIClient, '_get_comprehensive_track_data', return_value=mock_mbid_data):
            
            mock_network = MagicMock()
            mock_network.get_track_by_mbid.side_effect = mock_successful_get_track_by_mbid
            mock_network_class.return_value = mock_network
            
            # Test MBID comprehensive method
            result = await lastfm_client.get_track_info_comprehensive_by_mbid("real-mbid-456")
            
            # Should return the MBID data
            assert result == mock_mbid_data
            assert result['lastfm_mbid'] == 'real-mbid-456'
            assert result['lastfm_title'] == 'MBID Track'
            assert result['lastfm_artist_name'] == 'MBID Artist'

    @pytest.mark.asyncio
    async def test_love_track_success(self, lastfm_client):
        """Test successful track love operation."""
        
        def mock_successful_love_track(*args, **kwargs):
            """Mock successful track love."""
            return None  # love() doesn't return anything on success
        
        with patch('pylast.LastFMNetwork') as mock_network_class:
            mock_network = MagicMock()
            
            # Mock track.love() for love_track method
            mock_track = MagicMock()
            mock_track.love.side_effect = mock_successful_love_track
            mock_network.track.return_value = mock_track
            
            mock_network_class.return_value = mock_network
            
            # Test love_track
            result = await lastfm_client.love_track("Test Artist", "Test Track")
            assert result is True

