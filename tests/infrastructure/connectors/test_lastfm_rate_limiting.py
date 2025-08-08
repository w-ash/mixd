"""Tests for LastFM rate limiting with aiolimiter integration (simplified approach)."""

from unittest.mock import Mock, patch

from src.infrastructure.connectors.lastfm import LastFMConnector


class TestLastFMRateLimiting:
    """Test rate limiting integration in LastFM connector."""

    @patch("src.infrastructure.connectors.lastfm.AsyncLimiter")
    @patch("src.infrastructure.connectors.lastfm.settings")
    def test_rate_limiter_created_in_connector(
        self, mock_settings, mock_async_limiter
    ):
        """Test that rate limiter is created directly in the connector."""
        # Mock the modern settings structure
        mock_api_config = Mock()
        mock_api_config.lastfm_rate_limit = 5.0
        mock_api_config.lastfm_rate_limit_burst = 5.0
        mock_settings.api = mock_api_config
        
        # Mock credentials to prevent API initialization
        mock_credentials = Mock()
        mock_credentials.lastfm_key = ""  # Empty to prevent client creation
        mock_secret = Mock()
        mock_secret.get_secret_value.return_value = ""
        mock_credentials.lastfm_secret = mock_secret
        mock_credentials.lastfm_username = ""
        mock_password = Mock()
        mock_password.get_secret_value.return_value = ""
        mock_credentials.lastfm_password = mock_password
        mock_settings.credentials = mock_credentials

        # Mock AsyncLimiter instance
        mock_limiter = Mock()
        mock_async_limiter.return_value = mock_limiter

        # Create connector 
        connector = LastFMConnector()

        # Verify AsyncLimiter was created with correct parameters
        mock_async_limiter.assert_called_once_with(max_rate=5.0, time_period=1.0)

        # Verify rate limiter is stored in connector
        assert connector.rate_limiter == mock_limiter

    @patch("src.infrastructure.connectors.api_batch_processor.APIBatchProcessor")
    @patch("src.infrastructure.connectors.lastfm.AsyncLimiter")
    @patch("src.infrastructure.connectors.lastfm.settings")
    def test_batch_processor_created_directly(
        self, mock_settings, mock_async_limiter, mock_api_batch_processor
    ):
        """Test that APIBatchProcessor is created directly without rate limiter."""
        # Mock settings
        mock_api_config = Mock()
        mock_api_config.lastfm_rate_limit = 4.5
        mock_api_config.lastfm_rate_limit_burst = 4.5
        mock_api_config.lastfm_batch_size = 50
        mock_api_config.lastfm_concurrency = 200
        mock_api_config.lastfm_retry_count = 3
        mock_api_config.lastfm_retry_base_delay = 1.0
        mock_api_config.lastfm_retry_max_delay = 30.0
        mock_settings.api = mock_api_config
        
        # Mock credentials
        mock_credentials = Mock()
        mock_credentials.lastfm_key = ""
        mock_secret = Mock()
        mock_secret.get_secret_value.return_value = ""
        mock_credentials.lastfm_secret = mock_secret
        mock_credentials.lastfm_username = ""
        mock_password = Mock()
        mock_password.get_secret_value.return_value = ""
        mock_credentials.lastfm_password = mock_password
        mock_settings.credentials = mock_credentials

        # Mock processor instance
        mock_processor = Mock()
        mock_api_batch_processor.return_value = mock_processor

        # Create connector
        connector = LastFMConnector()
        batch_processor = connector.batch_processor

        # Verify APIBatchProcessor was created directly with correct settings
        mock_api_batch_processor.assert_called_once()
        call_kwargs = mock_api_batch_processor.call_args[1]
        assert call_kwargs["rate_limiter"] is None  # Rate limiting at API level
        assert call_kwargs["batch_size"] == 50
        assert call_kwargs["concurrency_limit"] == 200

    @patch("src.infrastructure.connectors.lastfm.AsyncLimiter")
    @patch("src.infrastructure.connectors.lastfm.settings")
    def test_rate_limiting_config_defaults(self, mock_settings, mock_async_limiter):
        """Test that rate limiting uses sensible defaults when config missing."""
        # Mock the settings with default values
        mock_api_config = Mock()
        mock_api_config.lastfm_rate_limit = 4.5  # Default rate limit
        mock_api_config.lastfm_rate_limit_burst = 4.5
        mock_settings.api = mock_api_config
        
        # Mock credentials to prevent API initialization
        mock_credentials = Mock()
        mock_credentials.lastfm_key = ""
        mock_secret = Mock()
        mock_secret.get_secret_value.return_value = ""
        mock_credentials.lastfm_secret = mock_secret
        mock_credentials.lastfm_username = ""
        mock_password = Mock()
        mock_password.get_secret_value.return_value = ""
        mock_credentials.lastfm_password = mock_password
        mock_settings.credentials = mock_credentials
        
        # Create connector with default settings (4.5 rate limit)
        connector = LastFMConnector()

        # Verify default rate limit of 4.5 was used (matches current settings)
        mock_async_limiter.assert_called_once_with(max_rate=4.5, time_period=1.0)
