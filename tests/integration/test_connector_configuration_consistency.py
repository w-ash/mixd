"""Integration tests for connector configuration consistency.

Tests to verify that the connector refactoring maintains consistent
configuration patterns across all services.
"""

from src.config import settings
from src.infrastructure.connectors.lastfm import LastFMConnector
from src.infrastructure.connectors.spotify import SpotifyConnector


class TestConnectorConfigurationConsistency:
    """Test that all connectors use consistent configuration patterns."""

    def test_spotify_uses_nested_connector_config(self):
        """Verify Spotify config lives under settings.api.spotify.* nested structure."""
        config = settings.api.spotify

        # Test nested ConnectorAPIConfig fields exist
        assert hasattr(config, "batch_size")
        assert hasattr(config, "retry_count")
        assert hasattr(config, "request_timeout")

        # Nested config exposes sensible typed values
        assert isinstance(config.batch_size, int)
        assert config.batch_size > 0

        assert isinstance(config.concurrency, int)
        assert config.concurrency > 0

        assert isinstance(config.retry_count, int)
        assert config.retry_count >= 0

    def test_lastfm_uses_nested_connector_config(self):
        """Verify LastFM config lives under settings.api.lastfm.* nested structure."""
        config = settings.api.lastfm

        # Test nested ConnectorAPIConfig fields exist
        assert hasattr(config, "batch_size")
        assert hasattr(config, "concurrency")
        assert hasattr(config, "retry_count")
        assert hasattr(config, "rate_limit")
        assert hasattr(config, "retry_base_delay")
        assert hasattr(config, "retry_max_delay")

        # Nested config exposes sensible typed values
        assert isinstance(config.batch_size, int)
        assert config.batch_size > 0

        # LastFM-specific rate limiting config
        assert isinstance(config.rate_limit, (int, float))
        assert config.rate_limit > 0

        assert isinstance(config.retry_base_delay, (int, float))
        assert config.retry_base_delay > 0

    def test_all_connectors_support_common_config_keys(self):
        """Verify all connectors expose the same basic configuration fields."""
        configs = [
            settings.api.spotify,
            settings.api.lastfm,
        ]

        common_fields = [
            "batch_size",
            "concurrency",
            "retry_count",
        ]

        for config in configs:
            for field in common_fields:
                value = getattr(config, field)
                assert value is not None
                assert isinstance(value, (int, float))
                assert value > 0

    def test_no_legacy_get_config_calls_in_connectors(self):
        """Verify connectors don't contain any legacy get_config() calls."""
        import inspect

        # Get source code for connector classes
        spotify_source = inspect.getsource(SpotifyConnector)
        lastfm_source = inspect.getsource(LastFMConnector)

        # Should not contain legacy imports or calls
        assert "from src.config import get_config" not in spotify_source
        assert "from src.config import get_config" not in lastfm_source
        assert "get_config(" not in spotify_source
        assert "get_config(" not in lastfm_source

    def test_error_classifiers_properly_integrated(self):
        """Verify all connectors have error classifiers properly integrated."""
        spotify = SpotifyConnector()
        lastfm = LastFMConnector()

        # Test error_classifier property exists and is callable
        assert hasattr(spotify, "error_classifier")
        assert hasattr(lastfm, "error_classifier")

        spotify_classifier = spotify.error_classifier
        lastfm_classifier = lastfm.error_classifier

        # Test classify_error method exists
        assert hasattr(spotify_classifier, "classify_error")
        assert hasattr(lastfm_classifier, "classify_error")

        # Test classifier handles basic exceptions
        test_exception = ValueError("Test error")

        spotify_result = spotify_classifier.classify_error(test_exception)
        assert isinstance(spotify_result, tuple)
        assert len(spotify_result) == 3  # (error_type, code, description)

        lastfm_result = lastfm_classifier.classify_error(test_exception)
        assert isinstance(lastfm_result, tuple)
        assert len(lastfm_result) == 3


class TestConnectorRetryIntegration:
    """Test that retry mechanisms are properly integrated across connectors.

    Note: Retry logic has been migrated to tenacity with centralized
    policies in RetryPolicyFactory. Individual connectors no longer have
    create_service_aware_retry() method - they use RetryPolicyFactory instead.
    """

    # Tests removed after migration to tenacity
