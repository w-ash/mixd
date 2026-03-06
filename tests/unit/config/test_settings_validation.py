"""Unit tests for settings field constraint validation.

Tests that Annotated type aliases (PositiveInt, ConfidenceScore, etc.) and
Literal log level types reject invalid values at construction time, while
accepting boundary values.
"""

from pydantic import ValidationError
import pytest

from src.config.settings import (
    APIConfig,
    BatchConfig,
    CLIConfig,
    ConnectorAPIConfig,
    FreshnessConfig,
    ImportConfig,
    LoggingConfig,
    MatchingConfig,
)


class TestDefaultsPassConstraints:
    """All default values should pass the new constraints."""

    def test_api_config_defaults(self):
        config = APIConfig()
        assert config.lastfm.batch_size == 50
        assert config.lastfm.rate_limit == 4.5

    def test_connector_api_config_defaults(self):
        config = ConnectorAPIConfig()
        assert config.batch_size == 50
        assert config.concurrency == 5
        assert config.rate_limit is None
        assert config.retry_count == 3

    def test_matching_config_defaults(self):
        config = MatchingConfig()
        assert config.base_confidence_isrc == 95
        assert config.high_similarity_threshold == 0.9

    def test_import_config_defaults(self):
        config = ImportConfig()
        assert config.play_threshold_percentage == 0.5
        assert config.batch_size == 1000

    def test_logging_config_defaults(self):
        config = LoggingConfig()
        assert config.console_level == "INFO"
        assert config.prefect_bridge_level == "DEBUG"

    def test_freshness_config_defaults(self):
        config = FreshnessConfig()
        assert config.lastfm_hours == 1.0
        assert config.spotify_hours == 24.0

    def test_batch_config_defaults(self):
        config = BatchConfig()
        assert config.truncation_limit == 5

    def test_cli_config_defaults(self):
        config = CLIConfig()
        assert config.playlist_name_min_width == 15


class TestConnectorAPIConfigNesting:
    """Verify per-connector defaults are applied correctly via APIConfig."""

    def test_lastfm_overrides(self):
        config = APIConfig()
        assert config.lastfm.concurrency == 200
        assert config.lastfm.rate_limit == 4.5
        assert config.lastfm.retry_count == 8
        assert config.lastfm.request_timeout == 30.0
        assert config.lastfm.retry_max_delay == 60.0

    def test_spotify_overrides(self):
        config = APIConfig()
        assert config.spotify.batch_size == 50
        assert config.spotify.concurrency == 50
        assert config.spotify.request_delay == 0.1
        assert config.spotify.retry_base_delay == 0.5

    def test_musicbrainz_overrides(self):
        config = APIConfig()
        assert config.musicbrainz.concurrency == 5
        assert config.musicbrainz.request_delay == 0.2

    def test_spotify_specific_fields(self):
        config = APIConfig()
        assert config.spotify_large_batch_size == 100
        assert config.spotify_market == "US"


class TestBoundaryAcceptance:
    """Values at constraint boundaries should be accepted."""

    def test_percentage_zero(self):
        config = ImportConfig(play_threshold_percentage=0.0)
        assert config.play_threshold_percentage == 0.0

    def test_percentage_one(self):
        config = ImportConfig(play_threshold_percentage=1.0)
        assert config.play_threshold_percentage == 1.0

    def test_confidence_zero(self):
        config = MatchingConfig(base_confidence_isrc=0)
        assert config.base_confidence_isrc == 0

    def test_confidence_hundred(self):
        config = MatchingConfig(base_confidence_isrc=100)
        assert config.base_confidence_isrc == 100

    def test_similarity_zero(self):
        config = MatchingConfig(high_similarity_threshold=0.0)
        assert config.high_similarity_threshold == 0.0

    def test_similarity_one(self):
        config = MatchingConfig(high_similarity_threshold=1.0)
        assert config.high_similarity_threshold == 1.0

    def test_positive_int_minimum(self):
        config = ConnectorAPIConfig(batch_size=1)
        assert config.batch_size == 1

    def test_positive_float_tiny(self):
        config = ConnectorAPIConfig(rate_limit=0.001)
        assert config.rate_limit == 0.001

    def test_non_negative_int_zero(self):
        config = ConnectorAPIConfig(retry_count=0)
        assert config.retry_count == 0

    def test_non_negative_float_zero(self):
        config = ConnectorAPIConfig(request_delay=0.0)
        assert config.request_delay == 0.0

    def test_freshness_zero_hours(self):
        config = FreshnessConfig(lastfm_hours=0.0)
        assert config.lastfm_hours == 0.0


class TestPositiveIntRejection:
    """Fields typed as PositiveInt must reject 0 and negative values."""

    def test_batch_size_zero(self):
        with pytest.raises(ValidationError):
            ConnectorAPIConfig(batch_size=0)

    def test_batch_size_negative(self):
        with pytest.raises(ValidationError):
            ConnectorAPIConfig(batch_size=-1)

    def test_concurrency_zero(self):
        with pytest.raises(ValidationError):
            ConnectorAPIConfig(concurrency=0)

    def test_truncation_limit_zero(self):
        with pytest.raises(ValidationError):
            BatchConfig(truncation_limit=0)

    def test_import_batch_size_zero(self):
        with pytest.raises(ValidationError):
            ImportConfig(batch_size=0)

    def test_cli_min_width_zero(self):
        with pytest.raises(ValidationError):
            CLIConfig(playlist_name_min_width=0)

    def test_full_history_threshold_zero(self):
        with pytest.raises(ValidationError):
            ImportConfig(full_history_import_threshold=0)


class TestPositiveFloatRejection:
    """Fields typed as PositiveFloat must reject 0.0 and negative values."""

    def test_rate_limit_zero(self):
        with pytest.raises(ValidationError):
            ConnectorAPIConfig(rate_limit=0.0)

    def test_rate_limit_negative(self):
        with pytest.raises(ValidationError):
            ConnectorAPIConfig(rate_limit=-1.0)

    def test_timeout_zero(self):
        with pytest.raises(ValidationError):
            ConnectorAPIConfig(request_timeout=0.0)

    def test_max_delay_zero(self):
        with pytest.raises(ValidationError):
            ConnectorAPIConfig(retry_max_delay=0.0)


class TestConfidenceScoreRejection:
    """ConfidenceScore fields must be 0-100."""

    def test_over_hundred(self):
        with pytest.raises(ValidationError):
            MatchingConfig(base_confidence_isrc=101)

    def test_negative(self):
        with pytest.raises(ValidationError):
            MatchingConfig(base_confidence_isrc=-1)

    def test_penalty_over_hundred(self):
        with pytest.raises(ValidationError):
            MatchingConfig(duration_max_penalty=101)

    def test_threshold_over_hundred(self):
        with pytest.raises(ValidationError):
            MatchingConfig(threshold_artist_title=101)


class TestPercentageRejection:
    """Percentage fields must be 0.0-1.0."""

    def test_over_one(self):
        with pytest.raises(ValidationError):
            ImportConfig(play_threshold_percentage=1.5)

    def test_negative(self):
        with pytest.raises(ValidationError):
            ImportConfig(play_threshold_percentage=-0.1)


class TestSimilarityScoreRejection:
    """SimilarityScore fields must be 0.0-1.0."""

    def test_over_one(self):
        with pytest.raises(ValidationError):
            MatchingConfig(high_similarity_threshold=1.1)

    def test_negative(self):
        with pytest.raises(ValidationError):
            MatchingConfig(high_similarity_threshold=-0.1)

    def test_variation_score_over_one(self):
        with pytest.raises(ValidationError):
            MatchingConfig(variation_similarity_score=1.5)


class TestLogLevelValidation:
    """Log level fields must be valid Literal values."""

    def test_invalid_console_level(self):
        with pytest.raises(ValidationError):
            LoggingConfig(console_level="INVALID")

    def test_invalid_file_level(self):
        with pytest.raises(ValidationError):
            LoggingConfig(file_level="VERBOSE")

    def test_loguru_levels_accepted_for_console(self):
        """TRACE and SUCCESS are Loguru-specific but valid for console/file."""
        config = LoggingConfig(console_level="TRACE")
        assert config.console_level == "TRACE"
        config = LoggingConfig(console_level="SUCCESS")
        assert config.console_level == "SUCCESS"

    def test_trace_rejected_for_prefect_bridge(self):
        """TRACE is Loguru-only; Prefect bridge uses getattr(logging, level)."""
        with pytest.raises(ValidationError):
            LoggingConfig(prefect_bridge_level="TRACE")

    def test_success_rejected_for_prefect_logger(self):
        """SUCCESS is Loguru-only; Prefect logger uses getattr(logging, level)."""
        with pytest.raises(ValidationError):
            LoggingConfig(prefect_logger_level="SUCCESS")

    def test_valid_stdlib_levels_for_prefect(self):
        for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            config = LoggingConfig(
                prefect_bridge_level=level, prefect_logger_level=level
            )
            assert config.prefect_bridge_level == level


class TestEnvIgnoreEmpty:
    """env_ignore_empty=True should treat empty env vars as unset."""

    def test_empty_env_var_uses_default(self, monkeypatch: pytest.MonkeyPatch):
        """An empty env var for a nested connector field should fall back to default."""
        monkeypatch.setenv("API__LASTFM__BATCH_SIZE", "")
        from src.config.settings import Settings

        s = Settings()
        # Should use default (50), not crash on int("")
        assert s.api.lastfm.batch_size == 50
