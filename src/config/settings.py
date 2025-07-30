"""Modern configuration management using Pydantic Settings.

This module provides type-safe configuration management with automatic
environment variable loading and validation using Pydantic Settings v2.10.1.

The configuration is organized into logical groups:
- DatabaseConfig: Database connection and pooling settings
- LoggingConfig: Logging levels, files, and debugging options
- APIConfig: External API configuration (LastFM, Spotify, MusicBrainz)
- BatchConfig: Batch processing and progress reporting settings
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseConfig(BaseModel):
    """Database connection and pooling configuration."""

    url: str = "sqlite+aiosqlite:///data/db/narada.db"
    echo: bool = False
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout: int = 30
    pool_recycle: int = 1800
    connect_retries: int = 3
    retry_interval: int = 5


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
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = "http://localhost:8888/callback"

    # LastFM credentials
    lastfm_key: str = ""
    lastfm_secret: str = ""
    lastfm_username: str = ""
    lastfm_password: str = ""


class APIConfig(BaseModel):
    """External API configuration and rate limiting."""

    # Global API defaults
    default_batch_size: int = 50
    default_concurrency: int = 5
    default_retry_count: int = 3
    default_retry_base_delay: float = 1.0
    default_retry_max_delay: float = 30.0
    default_request_delay: float = 0.2

    # LastFM API Configuration (rate limited to 5 calls/second)
    lastfm_batch_size: int = 50
    lastfm_concurrency: int = 1000  # High concurrency for in-flight requests
    lastfm_rate_limit: float = 5.0  # Calls per second (rate limiter)
    lastfm_retry_count: int = 3
    lastfm_retry_base_delay: float = 2.0
    lastfm_retry_max_delay: float = 60.0
    lastfm_max_retry_time: int = 60
    lastfm_love_track_retry_count: int = 3
    lastfm_recent_tracks_min_limit: int = 1
    lastfm_recent_tracks_max_limit: int = 200

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
    track_batch_retry_count: int = 3
    track_batch_retry_delay: int = 5


class ImportConfig(BaseModel):
    """Import processing and data quality configuration."""

    # Play filtering thresholds
    play_threshold_ms: int = 240000  # 4 minutes fallback threshold
    play_threshold_percentage: float = 0.5  # 50% of track duration

    # Import batch processing
    import_batch_size: int = 1000
    import_progress_frequency: int = 100


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
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
    )

    # Nested configuration groups
    database: DatabaseConfig = DatabaseConfig()
    logging: LoggingConfig = LoggingConfig()
    credentials: CredentialsConfig = CredentialsConfig()
    api: APIConfig = APIConfig()
    batch: BatchConfig = BatchConfig()
    import_settings: ImportConfig = ImportConfig()
    freshness: FreshnessConfig = FreshnessConfig()

    # Top-level settings
    data_dir: Path = Path("data")

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
            "database_echo": "echo",
            "database_pool_size": "pool_size",
            "database_max_overflow": "max_overflow",
            "database_pool_timeout": "pool_timeout",
            "database_pool_recycle": "pool_recycle",
            "database_connect_retries": "connect_retries",
            "database_retry_interval": "retry_interval",
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
# BACKWARD COMPATIBILITY FUNCTIONS
# =============================================================================

# Legacy key mapping for backward compatibility
_LEGACY_KEY_MAP = {
    # Database settings
    "DATABASE_URL": lambda: settings.database.url,
    "DATABASE_ECHO": lambda: settings.database.echo,
    "DATABASE_POOL_SIZE": lambda: settings.database.pool_size,
    "DATABASE_MAX_OVERFLOW": lambda: settings.database.max_overflow,
    "DATABASE_POOL_TIMEOUT": lambda: settings.database.pool_timeout,
    "DATABASE_POOL_RECYCLE": lambda: settings.database.pool_recycle,
    "DATABASE_CONNECT_RETRIES": lambda: settings.database.connect_retries,
    "DATABASE_RETRY_INTERVAL": lambda: settings.database.retry_interval,
    # Logging settings
    "CONSOLE_LOG_LEVEL": lambda: settings.logging.console_level,
    "FILE_LOG_LEVEL": lambda: settings.logging.file_level,
    "LOG_FILE": lambda: settings.logging.log_file,
    "LOG_REAL_TIME_DEBUG": lambda: settings.logging.real_time_debug,
    # Application settings
    "DATA_DIR": lambda: settings.data_dir,
    # Credentials
    "SPOTIFY_CLIENT_ID": lambda: settings.credentials.spotify_client_id,
    "SPOTIFY_CLIENT_SECRET": lambda: settings.credentials.spotify_client_secret,
    "SPOTIFY_REDIRECT_URI": lambda: settings.credentials.spotify_redirect_uri,
    "LASTFM_KEY": lambda: settings.credentials.lastfm_key,
    "LASTFM_SECRET": lambda: settings.credentials.lastfm_secret,
    "LASTFM_USERNAME": lambda: settings.credentials.lastfm_username,
    "LASTFM_PASSWORD": lambda: settings.credentials.lastfm_password,
    # Batch processing settings
    "BATCH_PROGRESS_LOG_FREQUENCY": lambda: settings.batch.progress_log_frequency,
    "TRACK_BATCH_RETRY_COUNT": lambda: settings.batch.track_batch_retry_count,
    "TRACK_BATCH_RETRY_DELAY": lambda: settings.batch.track_batch_retry_delay,
    # Import settings
    "PLAY_THRESHOLD_MS": lambda: settings.import_settings.play_threshold_ms,
    "PLAY_THRESHOLD_PERCENTAGE": lambda: settings.import_settings.play_threshold_percentage,
    "IMPORT_BATCH_SIZE": lambda: settings.import_settings.import_batch_size,
    "IMPORT_PROGRESS_FREQUENCY": lambda: settings.import_settings.import_progress_frequency,
    # Global API defaults
    "DEFAULT_API_BATCH_SIZE": lambda: settings.api.default_batch_size,
    "DEFAULT_API_CONCURRENCY": lambda: settings.api.default_concurrency,
    "DEFAULT_API_RETRY_COUNT": lambda: settings.api.default_retry_count,
    "DEFAULT_API_RETRY_BASE_DELAY": lambda: settings.api.default_retry_base_delay,
    "DEFAULT_API_RETRY_MAX_DELAY": lambda: settings.api.default_retry_max_delay,
    "DEFAULT_API_REQUEST_DELAY": lambda: settings.api.default_request_delay,
    # LastFM API settings
    "LASTFM_API_BATCH_SIZE": lambda: settings.api.lastfm_batch_size,
    "LASTFM_API_CONCURRENCY": lambda: settings.api.lastfm_concurrency,
    "LASTFM_API_RATE_LIMIT": lambda: settings.api.lastfm_rate_limit,
    "LASTFM_API_RETRY_COUNT": lambda: settings.api.lastfm_retry_count,
    "LASTFM_API_RETRY_BASE_DELAY": lambda: settings.api.lastfm_retry_base_delay,
    "LASTFM_API_RETRY_MAX_DELAY": lambda: settings.api.lastfm_retry_max_delay,
    "LASTFM_API_MAX_RETRY_TIME": lambda: settings.api.lastfm_max_retry_time,
    "LASTFM_LOVE_TRACK_RETRY_COUNT": lambda: settings.api.lastfm_love_track_retry_count,
    "LASTFM_RECENT_TRACKS_MIN_LIMIT": lambda: settings.api.lastfm_recent_tracks_min_limit,
    "LASTFM_RECENT_TRACKS_MAX_LIMIT": lambda: settings.api.lastfm_recent_tracks_max_limit,
    # Spotify API settings
    "SPOTIFY_API_BATCH_SIZE": lambda: settings.api.spotify_batch_size,
    "SPOTIFY_LARGE_BATCH_SIZE": lambda: settings.api.spotify_large_batch_size,
    "SPOTIFY_API_CONCURRENCY": lambda: settings.api.spotify_concurrency,
    "SPOTIFY_API_RETRY_COUNT": lambda: settings.api.spotify_retry_count,
    "SPOTIFY_API_RETRY_BASE_DELAY": lambda: settings.api.spotify_retry_base_delay,
    "SPOTIFY_API_RETRY_MAX_DELAY": lambda: settings.api.spotify_retry_max_delay,
    "SPOTIFY_API_REQUEST_DELAY": lambda: settings.api.spotify_request_delay,
    "SPOTIFY_API_REQUEST_TIMEOUT": lambda: settings.api.spotify_request_timeout,
    "SPOTIFY_RETRIES": lambda: settings.api.spotify_retries,
    "SPOTIFY_MARKET": lambda: settings.api.spotify_market,
    # MusicBrainz API settings
    "MUSICBRAINZ_API_BATCH_SIZE": lambda: settings.api.musicbrainz_batch_size,
    "MUSICBRAINZ_API_CONCURRENCY": lambda: settings.api.musicbrainz_concurrency,
    "MUSICBRAINZ_API_RETRY_COUNT": lambda: settings.api.musicbrainz_retry_count,
    "MUSICBRAINZ_API_RETRY_BASE_DELAY": lambda: settings.api.musicbrainz_retry_base_delay,
    "MUSICBRAINZ_API_RETRY_MAX_DELAY": lambda: settings.api.musicbrainz_retry_max_delay,
    "MUSICBRAINZ_API_REQUEST_DELAY": lambda: settings.api.musicbrainz_request_delay,
    # Data freshness settings
    "ENRICHER_DATA_FRESHNESS_LASTFM": lambda: settings.freshness.lastfm_hours,
    "ENRICHER_DATA_FRESHNESS_SPOTIFY": lambda: settings.freshness.spotify_hours,
    "ENRICHER_DATA_FRESHNESS_MUSICBRAINZ": lambda: settings.freshness.musicbrainz_hours,
}


def get_config(key: str, default=None):
    """Get configuration value by key with optional default.

    Provides backward compatibility with the old dictionary-based config access.
    Maps legacy flat keys to the new nested Pydantic settings structure.

    Args:
        key: Configuration key to retrieve
        default: Default value if key not found

    Returns:
        Configuration value or default

    Example:
        >>> batch_size = get_config("LASTFM_API_BATCH_SIZE", 50)
        >>> db_url = get_config("DATABASE_URL")
    """
    if key in _LEGACY_KEY_MAP:
        return _LEGACY_KEY_MAP[key]()

    # If key not found in legacy map, return default
    return default
