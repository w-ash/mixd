"""Unit tests for environment hardening: get_database_url, ServerConfig, startup warnings."""

from unittest.mock import patch

from loguru import logger
from pydantic import ValidationError
import pytest

from src.config.settings import (
    ServerConfig,
    get_database_url,
    log_startup_warnings,
    settings,
)


@pytest.fixture
def capture_logs():
    """Capture Loguru output into a list of message strings."""
    messages: list[str] = []
    handler_id = logger.add(lambda msg: messages.append(str(msg)), level="WARNING")
    yield messages
    logger.remove(handler_id)


class TestGetDatabaseUrl:
    """get_database_url() resolves DATABASE_URL from environ or settings default."""

    def test_returns_settings_default_when_env_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            result = get_database_url()
        assert result == settings.database.url

    def test_respects_environ_override(self):
        override = "postgresql+asyncpg://localhost/test_db"
        with patch.dict("os.environ", {"DATABASE_URL": override}):
            result = get_database_url()
        assert result == override

    def test_ignores_empty_environ(self):
        with patch.dict("os.environ", {"DATABASE_URL": ""}):
            result = get_database_url()
        assert result == settings.database.url


class TestServerConfig:
    """ServerConfig validates host/port with sensible defaults."""

    def test_defaults(self):
        config = ServerConfig()
        assert config.host == "0.0.0.0"  # noqa: S104
        assert config.port == 8000
        assert config.cors_origins == ["http://localhost:5173"]

    def test_port_rejects_zero(self):
        with pytest.raises(ValidationError):
            ServerConfig(port=0)

    def test_port_rejects_negative(self):
        with pytest.raises(ValidationError):
            ServerConfig(port=-1)

    def test_port_rejects_too_high(self):
        with pytest.raises(ValidationError):
            ServerConfig(port=65536)

    def test_port_accepts_boundaries(self):
        assert ServerConfig(port=1).port == 1
        assert ServerConfig(port=65535).port == 65535


class TestLogStartupWarnings:
    """log_startup_warnings() warns about unconfigured credentials."""

    def _make_credentials(self, *, spotify_id: str = "", lastfm_key: str = ""):
        from src.config.settings import CredentialsConfig

        return CredentialsConfig(spotify_client_id=spotify_id, lastfm_key=lastfm_key)

    def test_warns_when_spotify_unconfigured(self, capture_logs):
        creds = self._make_credentials(lastfm_key="some_key")
        with patch.object(settings, "credentials", creds):
            log_startup_warnings()
        combined = "".join(capture_logs)
        assert "Spotify not configured" in combined
        assert "Last.fm not configured" not in combined

    def test_warns_when_lastfm_unconfigured(self, capture_logs):
        creds = self._make_credentials(spotify_id="some_id")
        with patch.object(settings, "credentials", creds):
            log_startup_warnings()
        combined = "".join(capture_logs)
        assert "Last.fm not configured" in combined
        assert "Spotify not configured" not in combined

    def test_silent_when_all_configured(self, capture_logs):
        creds = self._make_credentials(spotify_id="some_id", lastfm_key="some_key")
        with patch.object(settings, "credentials", creds):
            log_startup_warnings()
        assert len(capture_logs) == 0
