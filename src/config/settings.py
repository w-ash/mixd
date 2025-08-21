"""Modern configuration management using Pydantic Settings.

This module provides type-safe configuration management with automatic
environment variable loading and validation using Pydantic Settings v2.11+.

The configuration is organized into logical groups:
- DatabaseConfig: Database connection and pooling settings
- LoggingConfig: Logging levels, files, and debugging options
- APIConfig: External API configuration (LastFM, Spotify, MusicBrainz)
- BatchConfig: Batch processing and progress reporting settings
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseConfig(BaseModel):
    """Database connection configuration."""

    url: str = "sqlite+aiosqlite:///data/db/narada.db"


class LoggingConfig(BaseModel):
    """Logging configuration for console and file output."""

    console_level: str = "INFO"
    file_level: str = "DEBUG"
    log_file: Path = Path("narada.log")
    real_time_debug: bool = True


class CredentialsConfig(BaseModel):
    """API credentials and authentication settings."""

    # Spotify credentials
    spotify_client_id: str = ""
    spotify_client_secret: SecretStr = Field(
        default=SecretStr(""), description="Spotify client secret (sensitive)"
    )
    spotify_redirect_uri: str = "http://localhost:8888/callback"

    # LastFM credentials
    lastfm_key: str = ""
    lastfm_secret: SecretStr = Field(
        default=SecretStr(""), description="LastFM API secret (sensitive)"
    )
    lastfm_username: str = ""
    lastfm_password: SecretStr = Field(
        default=SecretStr(""), description="LastFM password (sensitive)"
    )


class APIConfig(BaseModel):
    """External API configuration and rate limiting."""

    # LastFM API Configuration (rate limited to ~5 calls/second)
    lastfm_batch_size: int = 50  # Tracks per batch
    lastfm_concurrency: int = 200  # Max concurrent requests in-flight
    lastfm_rate_limit: float = (
        4.5  # Request starts per second (10% buffer from 5.0 limit)
    )
    lastfm_retry_count: int = 8  # Max retries for network/rate limit errors
    lastfm_retry_base_delay: float = 1.0  # Exponential backoff base delay (seconds)
    lastfm_retry_max_delay: float = 60.0  # Exponential backoff max delay (seconds)
    lastfm_request_delay: float = 0.2  # Delay between requests (seconds)
    lastfm_recent_tracks_min_limit: int = 1  # Min tracks per recent tracks API call
    lastfm_recent_tracks_max_limit: int = 200  # Max tracks per recent tracks API call

    # Spotify API Configuration
    spotify_batch_size: int = 50
    spotify_large_batch_size: int = 100  # For operations that support larger batches
    spotify_concurrency: int = 5
    spotify_retry_count: int = 3
    spotify_retry_base_delay: float = 0.5
    spotify_retry_max_delay: float = 30.0
    spotify_request_delay: float = 0.1
    spotify_request_timeout: int = 15  # HTTP request timeout in seconds
    spotify_retries: int = 5  # Number of retries for failed requests
    spotify_market: str = "US"  # Default market for API requests

    # MusicBrainz API Configuration
    musicbrainz_batch_size: int = 50
    musicbrainz_concurrency: int = 5
    musicbrainz_retry_count: int = 3
    musicbrainz_retry_base_delay: float = 1.0
    musicbrainz_retry_max_delay: float = 30.0
    musicbrainz_request_delay: float = 0.2


class BatchConfig(BaseModel):
    """Batch processing and progress reporting configuration."""

    progress_log_frequency: int = 10
    move_log_threshold: int = 10  # Only log moves if count is below this threshold
    truncation_limit: int = 5  # Number of items to show before truncating lists


class ImportConfig(BaseModel):
    """Import processing and data quality configuration."""

    # Play filtering thresholds
    play_threshold_ms: int = 240000  # 4 minutes fallback threshold
    play_threshold_percentage: float = 0.5  # 50% of track duration

    # Import batch processing (ImportBatchProcessor)
    batch_size: int = 1000  # Items per batch for file processing
    retry_count: int = 3  # Retry attempts for transient processing errors
    retry_base_delay: float = 1.0  # Base retry delay in seconds
    memory_limit_mb: int = 100  # Advisory memory limit per batch
    progress_frequency: int = 100
    
    # Warning thresholds
    memory_warning_threshold: int = 10000  # Warn if batch size exceeds this
    file_size_warning_mb: int = 100  # Warn if file size exceeds this (MB)
    full_history_import_threshold: int = 10000  # Threshold to detect full history imports


class MatchingConfig(BaseModel):
    """Track matching and confidence scoring configuration."""

    # Base confidence scores by match method
    base_confidence_isrc: int = 95
    base_confidence_mbid: int = 95
    base_confidence_artist_title: int = 90

    # Confidence thresholds for match acceptance
    threshold_isrc: int = 85
    threshold_mbid: int = 85
    threshold_artist_title: int = 50  # Reduced from 70 to handle version differences
    threshold_default: int = 50

    # Connector-specific threshold overrides
    threshold_spotify: int = 75
    threshold_lastfm: int = 50  # Reduced from 65 to handle version differences
    threshold_musicbrainz: int = 80

    # Duration penalty configuration
    duration_missing_penalty: int = 5  # Reduced from 10
    duration_max_penalty: int = 30  # Reduced from 60 to handle version differences
    duration_tolerance_ms: int = 1000
    duration_per_second_penalty: float = 0.5  # Reduced from 1.0

    # Similarity thresholds
    high_similarity_threshold: float = 0.9
    low_similarity_threshold: float = 0.4

    # Penalty caps
    title_max_penalty: int = 30
    artist_max_penalty: int = 30

    # Title similarity constants
    variation_similarity_score: float = 0.6
    identical_similarity_score: float = 1.0


class FreshnessConfig(BaseModel):
    """Data freshness configuration in hours."""

    lastfm_hours: float = 1.0  # 1 hour
    spotify_hours: float = 24.0  # 24 hours
    musicbrainz_hours: float = 168.0  # 1 week


class Settings(BaseSettings):
    """Main application settings with environment variable support.

    Environment variables can be set using flat naming (current) or nested naming:
    - Flat: DATABASE_URL, CONSOLE_LOG_LEVEL, LASTFM_API_BATCH_SIZE
    - Nested: DATABASE__URL, LOGGING__CONSOLE_LEVEL, API__LASTFM_BATCH_SIZE

    The .env file is automatically loaded for development convenience.
    """

    model_config = SettingsConfigDict(
        env_file=(
            ".env.local",
            ".env",
        ),  # Load .env.local first if it exists, then .env
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",  # Ignore extra environment variables
        validate_default=True,  # Validate default values
    )

    # Nested configuration groups
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    credentials: CredentialsConfig = Field(default_factory=CredentialsConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    batch: BatchConfig = Field(default_factory=BatchConfig)
    import_settings: ImportConfig = Field(default_factory=ImportConfig)
    matching: MatchingConfig = Field(default_factory=MatchingConfig)
    freshness: FreshnessConfig = Field(default_factory=FreshnessConfig)

    # Top-level settings
    data_dir: Path = Field(
        default=Path("data"), description="Application data directory"
    )

    @model_validator(mode="before")
    @classmethod
    def transform_flat_env_vars(cls, data: Any) -> Any:
        """Transform flat environment variables to nested structure.

        Handles legacy flat env vars (DATABASE_URL) and maps them to the
        nested structure expected by the models (database.url).
        """
        if not isinstance(data, dict):
            return data

        transformed = {}

        # Database mappings
        db_mapping = {
            "database_url": "url",
        }
        for env_key, field_key in db_mapping.items():
            if env_key in data:
                transformed.setdefault("database", {})[field_key] = data.pop(env_key)

        # Logging mappings
        log_mapping = {
            "console_log_level": "console_level",
            "file_log_level": "file_level",
            "log_file": "log_file",
            "log_real_time_debug": "real_time_debug",
        }
        for env_key, field_key in log_mapping.items():
            if env_key in data:
                transformed.setdefault("logging", {})[field_key] = data.pop(env_key)

        # Credentials mappings
        cred_mapping = {
            "spotify_client_id": "spotify_client_id",
            "spotify_client_secret": "spotify_client_secret",
            "spotify_redirect_uri": "spotify_redirect_uri",
            "lastfm_key": "lastfm_key",
            "lastfm_secret": "lastfm_secret",
            "lastfm_username": "lastfm_username",
            "lastfm_password": "lastfm_password",
        }
        for env_key, field_key in cred_mapping.items():
            if env_key in data:
                transformed.setdefault("credentials", {})[field_key] = data.pop(env_key)

        # Merge transformed nested structure back into data
        data.update(transformed)

        return data


# Singleton instance for application use
settings = Settings()

# Create data directory if it doesn't exist
settings.data_dir.mkdir(exist_ok=True)


# =============================================================================
# MODERN SETTINGS API
# =============================================================================
# Use the settings object directly for all configuration access:
#   settings.api.lastfm_batch_size
#   settings.database.url
#   settings.credentials.spotify_client_id
# etc.
