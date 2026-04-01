"""Modern configuration management using Pydantic Settings.

This module provides type-safe configuration management with automatic
environment variable loading and validation using Pydantic Settings v2.13+.

The configuration is organized into logical groups:
- DatabaseConfig: Database connection string
- LoggingConfig: Console/file levels, rotation, Prefect integration
- CredentialsConfig: Spotify and Last.fm API keys and secrets
- APIConfig: Rate limits, batch sizes, timeouts for external APIs
- BatchConfig: Display truncation for log output
- CLIConfig: CLI table formatting widths
- ImportConfig: Import directories, play thresholds, batch processing
- MatchingConfig: Track matching confidence scores and penalties
- FreshnessConfig: Cache TTLs for connector metadata
- ServerConfig: CORS and HTTP middleware
- SecurityConfig: Token encryption key
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: Pydantic settings validators, loguru config

import contextlib
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, cast

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# =============================================================================
# CONSTRAINED TYPE ALIASES
# =============================================================================
# Reusable Annotated types for startup validation. Pydantic merges constraints
# from Annotated metadata with class-level Field(default=..., description=...).

Percentage = Annotated[float, Field(ge=0.0, le=1.0)]
PositiveFloat = Annotated[float, Field(gt=0.0)]
NonNegativeFloat = Annotated[float, Field(ge=0.0)]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]
ConfidenceScore = Annotated[int, Field(ge=0, le=100)]
SimilarityScore = Annotated[float, Field(ge=0.0, le=1.0)]

StdlibLogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class DatabaseConfig(BaseModel):
    """Database connection configuration.

    Pool and engine settings are hardcoded in db_connection.py and should
    not be user-tunable.
    """

    url: str = Field(
        default="postgresql+psycopg://mixd:mixd@localhost:5432/mixd",
        description="SQLAlchemy connection URL (postgresql+psycopg://).",
    )


class LoggingConfig(BaseModel):
    """Logging configuration for console and file output.

    Uses structlog in stdlib integration mode — Prefect/Uvicorn/FastAPI logs
    flow through automatically. Console gets colorized output, file gets flat JSON.
    """

    console_level: StdlibLogLevel = Field(
        default="INFO",
        description="Minimum log level for console (stdout) output.",
    )
    file_level: StdlibLogLevel = Field(
        default="DEBUG",
        description="Minimum log level for the rotating JSON log file.",
    )
    log_file: Path = Field(
        default=Path("mixd.log"),
        description="Path to the application log file.",
    )
    rotation: str = Field(
        default="10 MB",
        description="Log file rotation trigger (e.g., '10 MB', '100 MB').",
    )
    retention: str = Field(
        default="1 week",
        description="How long rotated log files are kept (mapped to backup count).",
    )
    prefect_log_level: StdlibLogLevel = Field(
        default="DEBUG",
        description="Minimum level for Prefect framework logs (stdlib integration — no bridge needed).",
    )
    prefect_logger_level: StdlibLogLevel = Field(
        default="DEBUG",
        description="Minimum level applied to individual Prefect logger instances.",
    )


class CredentialsConfig(BaseModel):
    """API credentials and authentication settings."""

    spotify_client_id: str = Field(
        default="",
        description="Spotify OAuth client ID. Required for all Spotify operations.",
    )
    spotify_client_secret: SecretStr = Field(
        default=SecretStr(""),
        description="Spotify OAuth client secret. Required for all Spotify operations.",
    )
    spotify_redirect_uri: str = Field(
        default="http://127.0.0.1:8888/callback",
        description="OAuth callback URL registered in the Spotify developer dashboard.",
    )

    lastfm_key: str = Field(
        default="",
        description="Last.fm API key. Required for scrobble history and play counts.",
    )
    lastfm_secret: SecretStr = Field(
        default=SecretStr(""),
        description="Last.fm API shared secret. Required for authenticated operations (love/unlove).",
    )
    lastfm_username: str = Field(
        default="",
        description="Last.fm username. Required for importing listening history.",
    )
    lastfm_password: SecretStr = Field(
        default=SecretStr(""),
        description="Last.fm password. Required for authenticated write operations.",
    )


class ConnectorAPIConfig(BaseModel):
    """Per-connector API tuning: batch sizes, retry policy, rate limiting."""

    batch_size: PositiveInt = Field(
        default=50,
        description="Tracks per batch for API operations.",
    )
    concurrency: PositiveInt = Field(
        default=5,
        description="Maximum concurrent in-flight requests.",
    )
    rate_limit: PositiveFloat | None = Field(
        default=None,
        description="Request starts per second. None means no rate limiting.",
    )
    retry_count: NonNegativeInt = Field(
        default=3,
        description="Retry attempts for transient API errors.",
    )
    retry_base_delay: NonNegativeFloat = Field(
        default=1.0,
        description="Exponential backoff base delay in seconds.",
    )
    retry_max_delay: PositiveFloat = Field(
        default=30.0,
        description="Maximum backoff delay in seconds.",
    )
    request_delay: NonNegativeFloat = Field(
        default=0.0,
        description="Delay in seconds between sequential API calls.",
    )
    request_timeout: PositiveFloat = Field(
        default=15.0,
        description="HTTP request timeout in seconds.",
    )


class APIConfig(BaseModel):
    """External API configuration and rate limiting."""

    lastfm: ConnectorAPIConfig = Field(
        default_factory=lambda: ConnectorAPIConfig(
            batch_size=50,
            concurrency=200,
            rate_limit=4.5,
            retry_count=8,
            request_timeout=30.0,
            retry_max_delay=60.0,
        ),
        description="Last.fm API tuning. Higher concurrency + rate limit because Last.fm allows bursts within 5/s.",
    )
    spotify: ConnectorAPIConfig = Field(
        default_factory=lambda: ConnectorAPIConfig(
            batch_size=50,
            concurrency=50,
            request_delay=0.1,
            retry_base_delay=0.5,
        ),
        description="Spotify API tuning.",
    )
    musicbrainz: ConnectorAPIConfig = Field(
        default_factory=lambda: ConnectorAPIConfig(
            concurrency=5,
            request_delay=0.2,
        ),
        description="MusicBrainz API tuning. Conservative defaults — MusicBrainz rate-limits aggressively.",
    )

    # Spotify-specific fields that don't fit the common shape
    spotify_large_batch_size: PositiveInt = Field(
        default=100,
        description="Batch size for Spotify endpoints accepting up to 100 items (e.g., library check).",
    )
    spotify_market: str = Field(
        default="US",
        description="ISO 3166-1 alpha-2 country code for track availability and content filtering.",
    )


class BatchConfig(BaseModel):
    """Display truncation settings for log messages and diagnostic output."""

    truncation_limit: PositiveInt = Field(
        default=5,
        description="Maximum items shown in a list before truncating with '... and N more'.",
    )


class CLIConfig(BaseModel):
    """CLI display and formatting configuration."""

    playlist_name_min_width: PositiveInt = Field(
        default=15,
        description="Minimum column width for playlist names in CLI tables.",
    )
    playlist_description_max_width: PositiveInt = Field(
        default=40,
        description="Maximum column width for playlist descriptions in CLI tables.",
    )
    playlist_description_truncation_length: PositiveInt = Field(
        default=37,
        description="Characters to keep before appending '...' when truncating. Should equal playlist_description_max_width minus 3.",
    )


class ImportConfig(BaseModel):
    """Import processing and data quality configuration."""

    imports_dir: Path = Field(
        default=Path("data/imports"),
        description="Directory where pending import files (e.g., Spotify GDPR exports) are placed for processing.",
    )
    imported_dir: Path = Field(
        default=Path("data/imports/imported"),
        description="Archive directory. Successfully processed files are moved here.",
    )

    # Play filtering thresholds (aligned with Last.fm scrobbling standards)
    play_threshold_ms: NonNegativeInt = Field(
        default=240000,
        description="Minimum playback duration (ms) to count as a 'play' when track duration is unknown. Fallback for play_threshold_percentage.",
    )
    play_threshold_percentage: Percentage = Field(
        default=0.5,
        description="Fraction of track duration (0.0-1.0) that must be played to count as a listen. Primary threshold; play_threshold_ms is the fallback.",
    )

    # Batch processing
    batch_size: PositiveInt = Field(
        default=1000,
        description="Items per batch for file import processing.",
    )
    retry_count: NonNegativeInt = Field(
        default=3,
        description="Retry attempts for transient processing errors during import.",
    )
    retry_base_delay: NonNegativeFloat = Field(
        default=1.0,
        description="Base retry delay in seconds for import processing errors.",
    )

    # Warning thresholds
    file_size_warning_mb: PositiveInt = Field(
        default=100,
        description="Emit a warning if an import file exceeds this size in MB.",
    )
    full_history_import_threshold: PositiveInt = Field(
        default=10000,
        description="Track count at which a Last.fm import switches to full-history mode with optimized batching.",
    )


class MatchingConfig(BaseModel):
    """Track matching and confidence scoring configuration."""

    # Base confidence scores by match method
    base_confidence_isrc: ConfidenceScore = Field(
        default=95,
        description="Starting confidence for ISRC identifier matches.",
    )
    base_confidence_mbid: ConfidenceScore = Field(
        default=95,
        description="Starting confidence for MusicBrainz ID matches.",
    )
    base_confidence_artist_title: ConfidenceScore = Field(
        default=90,
        description="Starting confidence for fuzzy artist+title matches.",
    )
    isrc_suspect_base_confidence: ConfidenceScore = Field(
        default=80,
        description="Reduced starting confidence for ISRC matches flagged as suspect (e.g., duration mismatch suggesting remaster).",
    )

    # Three-zone classification thresholds
    auto_accept_threshold: ConfidenceScore = Field(
        default=85,
        description="Confidence above which matches are auto-accepted without review.",
    )
    review_threshold: ConfidenceScore = Field(
        default=50,
        description="Confidence above which matches are queued for human review (below auto_accept). Below this, matches are auto-rejected.",
    )

    # Legacy per-method thresholds (used as floor within review zone)
    threshold_isrc: ConfidenceScore = Field(
        default=60,
        description="Minimum confidence to accept an ISRC-based match after penalties.",
    )
    threshold_mbid: ConfidenceScore = Field(
        default=60,
        description="Minimum confidence to accept a MusicBrainz ID-based match after penalties.",
    )
    threshold_artist_title: ConfidenceScore = Field(
        default=50,
        description="Minimum confidence for artist+title fuzzy matches. Lower than ISRC/MBID to accommodate title variations across services.",
    )
    threshold_default: ConfidenceScore = Field(
        default=50,
        description="Fallback threshold when match method is unspecified.",
    )

    # Duration penalty configuration
    duration_missing_penalty: ConfidenceScore = Field(
        default=5,
        description="Confidence deduction when one track has no duration metadata.",
    )
    duration_max_penalty: ConfidenceScore = Field(
        default=30,
        description="Maximum confidence deduction from duration differences. Capped to prevent duration alone from rejecting strong matches.",
    )
    duration_tolerance_ms: NonNegativeInt = Field(
        default=1000,
        description="Duration difference in ms below which no penalty is applied.",
    )
    duration_per_second_penalty: NonNegativeFloat = Field(
        default=0.5,
        description="Confidence points deducted per second of duration difference beyond tolerance.",
    )

    # Similarity thresholds
    high_similarity_threshold: SimilarityScore = Field(
        default=0.9,
        description="String similarity ratio (0.0-1.0) above which titles or artists are considered effectively identical.",
    )

    # Penalty caps
    title_max_penalty: ConfidenceScore = Field(
        default=30,
        description="Maximum confidence deduction from title dissimilarity.",
    )
    artist_max_penalty: ConfidenceScore = Field(
        default=30,
        description="Maximum confidence deduction from artist name dissimilarity.",
    )

    # Phonetic matching
    phonetic_similarity_score: SimilarityScore = Field(
        default=0.85,
        description="Similarity score assigned when names match phonetically but not exactly (e.g., Björk vs Bjork).",
    )

    # Title similarity constants
    variation_similarity_score: SimilarityScore = Field(
        default=0.6,
        description="Similarity score assigned when titles are recognized as variations (remix, live, etc.).",
    )
    identical_similarity_score: SimilarityScore = Field(
        default=1.0,
        description="Similarity score assigned when titles are exact matches.",
    )


class ServerConfig(BaseModel):
    """HTTP server and middleware configuration."""

    host: str = Field(
        default="0.0.0.0",  # noqa: S104
        description="Server bind address. Use 0.0.0.0 for all interfaces (Docker), 127.0.0.1 for local only.",
    )
    port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        description="Server port.",
    )
    cors_origins: list[str] = Field(
        default=["http://localhost:5173"],
        description="Allowed CORS origins. Defaults to Vite dev server. Add production domains when deploying.",
    )
    neon_auth_url: str = Field(
        default="",
        description="Neon Auth service base URL. When set, all routes except health/auth require JWT. Empty = no auth (local dev).",
    )
    neon_auth_jwks_url: str = Field(
        default="",
        description="JWKS endpoint for JWT signature validation. Required when neon_auth_url is set.",
    )
    allowed_emails: str = Field(
        default="",
        description="Comma-separated email allowlist. Only these users can access the app. Empty = anyone with valid auth.",
    )


class FreshnessConfig(BaseModel):
    """How long cached connector metadata stays valid before re-fetching.

    Used by enrichment to skip redundant API calls for recently-updated tracks.
    """

    lastfm_hours: NonNegativeFloat = Field(
        default=1.0,
        description="Hours before Last.fm play counts are re-fetched during enrichment.",
    )
    spotify_hours: NonNegativeFloat = Field(
        default=24.0,
        description="Hours before Spotify metadata is re-fetched.",
    )


class SecurityConfig(BaseModel):
    """Security configuration for data protection."""

    token_encryption_key: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Fernet encryption key for OAuth tokens at rest. "
            "Generate via: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
        ),
    )


class Settings(BaseSettings):
    """Main application settings with environment variable support.

    Environment variables can be set using flat naming (current) or nested naming:
    - Flat: DATABASE_URL, CONSOLE_LOG_LEVEL, LASTFM_API_BATCH_SIZE
    - Nested: DATABASE__URL, LOGGING__CONSOLE_LEVEL, API__LASTFM_BATCH_SIZE

    The .env file is automatically loaded for development convenience.
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=(
            ".env.local",
            ".env",
        ),  # Load .env.local first if it exists, then .env
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",  # Ignore extra environment variables
        validate_default=True,  # Validate default values
        env_ignore_empty=True,  # Treat empty env vars (FOO=) as unset; use defaults
    )

    # Nested configuration groups
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    credentials: CredentialsConfig = Field(default_factory=CredentialsConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    batch: BatchConfig = Field(default_factory=BatchConfig)
    cli: CLIConfig = Field(default_factory=CLIConfig)
    import_settings: ImportConfig = Field(
        default_factory=ImportConfig
    )  # 'import' is a Python reserved word
    matching: MatchingConfig = Field(default_factory=MatchingConfig)
    freshness: FreshnessConfig = Field(default_factory=FreshnessConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    # Top-level settings
    data_dir: Path = Field(
        default=Path("data"), description="Application data directory"
    )
    workflow_log_dir: Path = Field(
        default=Path("data/logs/runs"),
        description="Directory for per-workflow-run JSONL log files.",
    )

    # Flat env var → nested group routing.  {env_key: (group, field_key)}
    # Identity mappings (key == field) use None as shorthand for "keep name".
    _FLAT_ENV_ROUTES: ClassVar[dict[str, tuple[str, str | None]]] = {
        # Database
        "database_url": ("database", "url"),
        # Logging (renamed fields)
        "console_log_level": ("logging", "console_level"),
        "file_log_level": ("logging", "file_level"),
        "log_file": ("logging", "log_file"),
        # Credentials (identity — flat name matches field name)
        "spotify_client_id": ("credentials", None),
        "spotify_client_secret": ("credentials", None),
        "spotify_redirect_uri": ("credentials", None),
        "lastfm_key": ("credentials", None),
        "lastfm_secret": ("credentials", None),
        "lastfm_username": ("credentials", None),
        "lastfm_password": ("credentials", None),
        # Server
        "server_host": ("server", "host"),
        "server_port": ("server", "port"),
        "cors_origins": ("server", None),
        # Neon Auth
        "neon_auth_url": ("server", None),
        "neon_auth_jwks_url": ("server", None),
        "allowed_emails": ("server", None),
        # Security
        "token_encryption_key": ("security", None),
    }

    @model_validator(mode="before")
    @classmethod
    def transform_flat_env_vars(cls, data: Any) -> Any:
        """Transform flat environment variables to nested structure.

        Handles flat env vars (DATABASE_URL, SPOTIFY_CLIENT_ID, etc.) and
        maps them to the nested structure expected by the models (database.url,
        credentials.spotify_client_id, etc.).

        Pydantic Settings v2 only passes .env file content through the model
        validator — OS environment variables bypass it and are matched directly
        by field name with env_nested_delimiter. This means flat env vars from
        the OS (e.g., on Fly.io where there are no .env files) would be missed.

        To handle both sources, we also read from os.environ for any flat keys
        not already present in the data dict. JSON strings (e.g., '["url"]')
        are parsed so that complex types (list, dict) validate correctly.
        """
        import json
        import os

        if not isinstance(data, dict):
            return data
        data = cast(dict[str, Any], data)
        transformed: dict[str, Any] = {}

        for env_key, (group, field_key) in cls._FLAT_ENV_ROUTES.items():
            # Check data dict first (.env file content), then OS environment
            if env_key in data:
                value = data.pop(env_key)
            elif (os_value := os.environ.get(env_key.upper())) is not None:
                value = os_value
            else:
                continue

            # Parse JSON strings for complex types (lists, dicts)
            if isinstance(value, str) and value.startswith(("[", "{")):
                with contextlib.suppress(json.JSONDecodeError, ValueError):
                    value = json.loads(value)

            transformed.setdefault(group, {})[field_key or env_key] = value

        data.update(transformed)
        return data


# Singleton instance for application use
settings = Settings()

# Create data directory if it doesn't exist
settings.data_dir.mkdir(exist_ok=True)

# =============================================================================
# HELPERS
# =============================================================================


def _normalize_database_url(url: str) -> str:
    """Normalize DATABASE_URL variants to the ``postgresql+psycopg://`` scheme.

    Handles common variants:
    - ``postgres://`` (Fly.io, Heroku) → ``postgresql+psycopg://``
    - ``postgresql://`` (plain) → ``postgresql+psycopg://``
    - ``postgresql+psycopg_async://`` (legacy mixd) → ``postgresql+psycopg://``

    psycopg3's ``+psycopg`` dialect works for both ``create_engine()`` and
    ``create_async_engine()`` — SQLAlchemy auto-selects sync/async mode.
    """
    if not url:
        return url
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql+psycopg_async://"):
        return url.replace("+psycopg_async://", "+psycopg://", 1)
    return url


def get_database_url() -> str:
    """Get the database URL, respecting runtime environment overrides.

    Reads from os.environ first to support test fixtures that modify
    DATABASE_URL after the settings singleton is created at import time.
    Falls back to the settings default. Normalizes all URL variants to
    ``postgresql+psycopg://``.
    """
    import os

    raw = os.environ.get("DATABASE_URL", "") or settings.database.url
    return _normalize_database_url(raw)


def get_sync_database_url() -> str:
    """Get a plain ``postgresql://`` URL for raw psycopg.connect() calls.

    Strips the SQLAlchemy driver suffix so the URL works with psycopg3
    directly (CLI completions, one-off scripts). Returns empty string
    if no URL is configured.
    """
    url = get_database_url()
    if not url:
        return ""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def log_startup_warnings() -> None:
    """Log warnings for unconfigured services. Called once at startup."""
    from src.config.logging import get_logger

    logger = get_logger(__name__)

    if not settings.credentials.spotify_client_id:
        logger.warning("Spotify not configured — Spotify features will be unavailable")
    if not settings.credentials.lastfm_key:
        logger.warning(
            "Last.fm not configured — scrobble and play count features will be unavailable"
        )
    if (
        settings.server.neon_auth_url
        and not settings.security.token_encryption_key.get_secret_value()
    ):
        logger.warning(
            "Token encryption not configured — OAuth tokens stored as plaintext. "
            "Set TOKEN_ENCRYPTION_KEY for production use."
        )


# =============================================================================
# MODERN SETTINGS API
# =============================================================================
# Use the settings object directly for all configuration access:
#   settings.api.lastfm.batch_size
#   settings.database.url
#   settings.credentials.spotify_client_id
# etc.
