"""Tests for LastFM rate limiting with aiolimiter integration."""

from unittest.mock import AsyncMock, Mock, patch

from src.infrastructure.connectors.lastfm import LastFMConnector


class TestLastFMRateLimiting:
    """Test rate limiting integration in LastFM connector."""

    @patch("src.infrastructure.connectors.lastfm.AsyncLimiter")
    @patch("src.infrastructure.connectors.lastfm.get_config")
    def test_rate_limiter_created_with_correct_config(
        self, mock_get_config, mock_async_limiter
    ):
        """Test that rate limiter is created with configured rate limit."""
        # Mock config values
        mock_get_config.side_effect = lambda key, default=None: {
            "LASTFM_API_RATE_LIMIT": 5.0,
            "LASTFM_API_BATCH_SIZE": 50,
            "LASTFM_API_CONCURRENCY": 1000,
            "LASTFM_API_RETRY_COUNT": 3,
            "LASTFM_API_RETRY_BASE_DELAY": 2.0,
            "LASTFM_API_RETRY_MAX_DELAY": 60.0,
            "LASTFM_API_REQUEST_DELAY": 0.0,
        }.get(key, default)

        # Mock AsyncLimiter instance
        mock_limiter = Mock()
        mock_async_limiter.return_value = mock_limiter

        # Create connector (triggers __attrs_post_init__)
        connector = LastFMConnector()

        # Verify AsyncLimiter was created with correct parameters
        mock_async_limiter.assert_called_once_with(5.0, 1)  # 5 calls per 1 second

        # Verify rate limiter is stored in connector
        assert connector._api_rate_limiter == mock_limiter

    @patch("src.infrastructure.connectors.lastfm.AsyncLimiter")
    @patch("src.infrastructure.connectors.lastfm.BatchProcessor")
    @patch("src.infrastructure.connectors.lastfm.get_config")
    def test_batch_processor_uses_shared_rate_limiter(
        self, mock_get_config, mock_batch_processor, mock_async_limiter
    ):
        """Test that BatchProcessor receives the same rate limiter instance."""
        # Mock config values
        mock_get_config.side_effect = lambda key, default=None: {
            "LASTFM_API_RATE_LIMIT": 5.0,
            "LASTFM_API_BATCH_SIZE": 50,
            "LASTFM_API_CONCURRENCY": 1000,
            "LASTFM_API_RETRY_COUNT": 3,
            "LASTFM_API_RETRY_BASE_DELAY": 2.0,
            "LASTFM_API_RETRY_MAX_DELAY": 60.0,
            "LASTFM_API_REQUEST_DELAY": 0.0,
        }.get(key, default)

        # Mock AsyncLimiter instance
        mock_limiter = Mock()
        mock_async_limiter.return_value = mock_limiter

        # Mock BatchProcessor instance
        mock_processor = Mock()
        mock_batch_processor.return_value = mock_processor

        # Create connector
        LastFMConnector()

        # Verify BatchProcessor was initialized with the rate limiter
        # The call is made on the generic type subscript result
        generic_call = mock_batch_processor.__getitem__.return_value
        generic_call.assert_called_once()
        call_kwargs = generic_call.call_args[1]
        assert call_kwargs["rate_limiter"] == mock_limiter
        assert call_kwargs["concurrency_limit"] == 1000  # High concurrency
        assert call_kwargs["request_delay"] == 0.0  # No artificial delay

    @patch("src.infrastructure.connectors.lastfm.get_config")
    def test_rate_limiting_config_defaults(self, mock_get_config):
        """Test that rate limiting uses sensible defaults when config missing."""
        # Mock config to return defaults
        mock_get_config.side_effect = lambda key, default=None: default

        with patch(
            "src.infrastructure.connectors.lastfm.AsyncLimiter"
        ) as mock_async_limiter:
            # Create connector
            LastFMConnector()

            # Verify default rate limit of 5.0 was used
            mock_async_limiter.assert_called_once_with(5.0, 1)

    async def test_rate_limited_api_call_wrapper(self):
        """Test that _rate_limited_api_call properly uses the rate limiter."""
        # Create a real connector but mock the rate limiter
        with patch(
            "src.infrastructure.connectors.lastfm.AsyncLimiter"
        ) as mock_async_limiter:
            mock_limiter = AsyncMock()
            mock_async_limiter.return_value = mock_limiter

            connector = LastFMConnector()

            # Mock an API function
            mock_api_func = AsyncMock(return_value="test_result")

            # Call the rate-limited wrapper
            result = await connector._rate_limited_api_call(
                mock_api_func, "arg1", "arg2", kwarg1="value1"
            )

            # Verify rate limiter was used as context manager
            mock_limiter.__aenter__.assert_called_once()
            mock_limiter.__aexit__.assert_called_once()

            # Verify API function was called with correct arguments
            mock_api_func.assert_called_once_with("arg1", "arg2", kwarg1="value1")

            # Verify result was returned
            assert result == "test_result"
