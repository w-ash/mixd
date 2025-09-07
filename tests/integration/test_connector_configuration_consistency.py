"""Integration tests for connector configuration consistency.

Tests to verify that the connector refactoring maintains consistent
configuration patterns across all services.
"""

import pytest

from src.config import settings
from src.infrastructure.connectors.lastfm import LastFMConnector
from src.infrastructure.connectors.spotify import SpotifyConnector


@pytest.mark.integration
class TestConnectorConfigurationConsistency:
    """Test that all connectors use consistent configuration patterns."""

    def test_spotify_uses_modern_settings_structure(self):
        """Verify Spotify connector uses settings.api.spotify_* patterns."""
        connector = SpotifyConnector()

        # Test key configuration mappings exist in settings
        assert hasattr(settings.api, "spotify_batch_size")
        assert hasattr(settings.api, "spotify_concurrency")
        assert hasattr(settings.api, "spotify_retry_count")
        assert hasattr(settings.api, "spotify_request_timeout")

        # Test connector can access config without errors
        batch_size = connector.get_connector_config("BATCH_SIZE", 50)
        assert isinstance(batch_size, int)
        assert batch_size > 0

        concurrency = connector.get_connector_config("CONCURRENCY", 5)
        assert isinstance(concurrency, int)
        assert concurrency > 0

        retry_count = connector.get_connector_config("RETRY_COUNT", 3)
        assert isinstance(retry_count, int)
        assert retry_count >= 0

    def test_lastfm_uses_modern_settings_structure(self):
        """Verify LastFM connector uses settings.api.lastfm_* patterns."""
        connector = LastFMConnector()

        # Test key configuration mappings exist in settings
        assert hasattr(settings.api, "lastfm_batch_size")
        assert hasattr(settings.api, "lastfm_concurrency")
        assert hasattr(settings.api, "lastfm_retry_count_rate_limit")
        assert hasattr(settings.api, "lastfm_retry_count_network")
        assert hasattr(settings.api, "lastfm_rate_limit")
        assert hasattr(settings.api, "lastfm_retry_base_delay")
        assert hasattr(settings.api, "lastfm_retry_max_delay")

        # Test connector can access config without errors
        batch_size = connector.get_connector_config("BATCH_SIZE", 30)
        assert isinstance(batch_size, int)
        assert batch_size > 0

        # Test LastFM-specific rate limiting config
        rate_limit = connector.get_connector_config("RATE_LIMIT", 4.5)
        assert isinstance(rate_limit, (int, float))
        assert rate_limit > 0

        retry_base_delay = connector.get_connector_config("RETRY_BASE_DELAY", 1.0)
        assert isinstance(retry_base_delay, (int, float))
        assert retry_base_delay > 0

    def test_all_connectors_support_common_config_keys(self):
        """Verify all connectors support the same basic configuration keys."""
        connectors = [
            SpotifyConnector(),
            LastFMConnector(),
        ]

        common_keys = [
            "BATCH_SIZE",
            "CONCURRENCY",
            "RETRY_COUNT",
        ]

        for connector in connectors:
            for key in common_keys:
                # Should not raise exceptions
                value = connector.get_connector_config(key, 10)
                assert value is not None
                assert isinstance(value, (int, float))
                assert value > 0

    def test_connector_config_provides_sensible_defaults(self):
        """Verify connectors provide sensible defaults when settings are missing."""
        spotify = SpotifyConnector()
        lastfm = LastFMConnector()

        # Test with non-existent keys to verify default behavior
        fake_key = "NON_EXISTENT_KEY"
        default_value = 42

        spotify_result = spotify.get_connector_config(fake_key, default_value)
        assert spotify_result == default_value

        lastfm_result = lastfm.get_connector_config(fake_key, default_value)
        assert lastfm_result == default_value

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
    """Test that retry mechanisms are properly integrated across connectors."""

    def test_connectors_create_service_aware_retry_decorators(self):
        """Verify connectors can create service-aware retry decorators."""
        spotify = SpotifyConnector()
        lastfm = LastFMConnector()

        # Test create_service_aware_retry method exists
        assert hasattr(spotify, "create_service_aware_retry")
        assert hasattr(lastfm, "create_service_aware_retry")

        # Test method returns callable decorator
        spotify_retry = spotify.create_service_aware_retry(max_tries=3, base_delay=1.0)
        lastfm_retry = lastfm.create_service_aware_retry(max_tries=3, base_delay=1.0)

        assert callable(spotify_retry)
        assert callable(lastfm_retry)
