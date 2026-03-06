"""Unit tests for import configuration settings.

Tests ensure that play filtering configuration is properly loaded
and accessible through the settings system.
"""

from src.config.settings import ImportConfig, settings


class TestImportConfig:
    """Test import configuration model and defaults."""

    def test_import_config_defaults(self):
        """Test that ImportConfig has sensible defaults."""
        config = ImportConfig()

        # 4 minutes in milliseconds
        assert config.play_threshold_ms == 240_000

        # 50% threshold
        assert config.play_threshold_percentage == 0.5

        # Batch processing defaults
        assert config.batch_size == 1000

    def test_import_config_custom_values(self):
        """Test that ImportConfig accepts custom values."""
        config = ImportConfig(
            play_threshold_ms=180_000,  # 3 minutes
            play_threshold_percentage=0.6,  # 60%
            batch_size=500,
        )

        assert config.play_threshold_ms == 180_000
        assert config.play_threshold_percentage == 0.6
        assert config.batch_size == 500

    def test_play_threshold_ms_validation(self):
        """Test that play threshold is reasonable."""
        config = ImportConfig()

        # Should be at least 30 seconds (too low would filter everything)
        assert config.play_threshold_ms >= 30_000

        # Should be no more than 10 minutes (too high would filter nothing)
        assert config.play_threshold_ms <= 600_000

    def test_play_threshold_percentage_validation(self):
        """Test that percentage threshold is reasonable."""
        config = ImportConfig()

        # Should be between 0 and 1
        assert 0.0 <= config.play_threshold_percentage <= 1.0

        # Should be reasonable for music (not too low/high)
        assert 0.3 <= config.play_threshold_percentage <= 0.8


class TestSettingsAccessibility:
    """Test that configuration values are accessible through settings."""

    def test_get_play_threshold_ms(self):
        """Test accessing play threshold through settings."""
        value = settings.import_settings.play_threshold_ms

        # Should return the default value
        assert value == 240_000

        # Should be an integer
        assert isinstance(value, int)

    def test_get_play_threshold_percentage(self):
        """Test accessing percentage threshold through settings."""
        value = settings.import_settings.play_threshold_percentage

        # Should return the default value
        assert value == 0.5

        # Should be a float
        assert isinstance(value, float)

    def test_settings_structure_completeness(self):
        """Test that settings structure provides all needed values."""
        # Import settings should be accessible
        assert hasattr(settings, "import_settings")
        assert hasattr(settings.import_settings, "play_threshold_ms")
        assert hasattr(settings.import_settings, "play_threshold_percentage")
        assert hasattr(settings.import_settings, "batch_size")

        # Values should be proper types
        assert isinstance(settings.import_settings.play_threshold_ms, int)
        assert isinstance(settings.import_settings.play_threshold_percentage, float)


class TestConfigurationConsistency:
    """Test that configuration values are consistent with business logic."""

    def test_threshold_relationship(self):
        """Test that thresholds make sense relative to each other."""
        ms_threshold = settings.import_settings.play_threshold_ms
        percentage_threshold = settings.import_settings.play_threshold_percentage

        # For a typical 4-minute song (240,000 ms):
        typical_song_duration = 240_000
        percentage_of_typical = typical_song_duration * percentage_threshold

        # The percentage threshold should be reasonable relative to the time threshold
        # For 50% of 4-minute song = 2 minutes = 120,000 ms
        # This should be less than the 4-minute fallback threshold
        assert percentage_of_typical < ms_threshold

    def test_configuration_matches_business_rules(self):
        """Test that config values match the business rules in implementation."""
        # These should match the values used in should_include_play function
        assert settings.import_settings.play_threshold_ms == 240_000  # 4 minutes
        assert settings.import_settings.play_threshold_percentage == 0.5  # 50%

        # These match Last.fm's scrobbling standards:
        # - 4 minutes OR 50% of track duration, whichever is shorter
