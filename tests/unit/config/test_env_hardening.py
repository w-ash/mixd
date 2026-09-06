"""Unit tests for environment hardening: get_database_url, ServerConfig, startup warnings."""

import os
from pathlib import Path
from typing import ClassVar, cast
from unittest.mock import patch

from pydantic import ValidationError
import pytest
import structlog

from src.config.settings import (
    ServerConfig,
    Settings,
    get_database_url,
    log_startup_warnings,
    settings,
)


@pytest.fixture
def capture_logs():
    """Capture structlog output into a list of event dicts."""
    with structlog.testing.capture_logs() as logs:
        yield logs


class TestGetDatabaseUrl:
    """get_database_url() resolves DATABASE_URL from environ or settings default.

    Both inputs are pinned explicitly. Asserting the result against the ambient
    ``settings.database.url`` instead would be vacuous wherever that value is
    empty (CI, which has no .env) and wrong wherever it is set un-normalized
    (any developer machine with a plain ``postgresql://`` in .env.local).
    """

    SETTINGS_RAW = "postgresql://settings-host/settings_db"
    SETTINGS_NORMALIZED = "postgresql+psycopg://settings-host/settings_db"

    def test_falls_back_to_settings_when_env_unset(self):
        with (
            patch.object(settings.database, "url", self.SETTINGS_RAW),
            patch.dict("os.environ", {}, clear=True),
        ):
            assert get_database_url() == self.SETTINGS_NORMALIZED

    def test_ignores_empty_environ(self):
        with (
            patch.object(settings.database, "url", self.SETTINGS_RAW),
            patch.dict("os.environ", {"DATABASE_URL": ""}),
        ):
            assert get_database_url() == self.SETTINGS_NORMALIZED

    def test_respects_environ_override(self):
        override = "postgresql+asyncpg://localhost/test_db"
        with (
            patch.object(settings.database, "url", self.SETTINGS_RAW),
            patch.dict("os.environ", {"DATABASE_URL": override}),
        ):
            # An unrecognized driver suffix passes through untouched, so this
            # also pins that normalization never rewrites a driver we don't own.
            assert get_database_url() == override

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("postgres://h/db", "postgresql+psycopg://h/db"),
            ("postgresql://h/db", "postgresql+psycopg://h/db"),
            ("postgresql+psycopg_async://h/db", "postgresql+psycopg://h/db"),
            ("postgresql+psycopg://h/db", "postgresql+psycopg://h/db"),
            ("", ""),
        ],
    )
    def test_normalizes_url_variants(self, raw: str, expected: str):
        with patch.dict("os.environ", {"DATABASE_URL": raw}):
            # Empty environ falls through to settings, so pin that empty too.
            with patch.object(settings.database, "url", ""):
                assert get_database_url() == expected


class TestFlatEnvPrecedence:
    """Flat keys resolve shell > .env.local > .env.

    Worth pinning because .env.local points at production on a developer
    machine: an export that loses to it silently redirects a command meant to
    be local.
    """

    DOTENV_URL = "postgresql+psycopg://dotenv-host/dotenv_db"
    SHELL_URL = "postgresql+psycopg://localhost:5432/mixd"

    @staticmethod
    def _dotenv(tmp_path: Path) -> Path:
        path = tmp_path / ".env.test"
        _ = path.write_text(f"DATABASE_URL={TestFlatEnvPrecedence.DOTENV_URL}\n")
        return path

    def test_shell_export_wins(self, tmp_path: Path):
        with patch.dict("os.environ", {"DATABASE_URL": self.SHELL_URL}):
            resolved = Settings(_env_file=self._dotenv(tmp_path)).database.url
        assert resolved == self.SHELL_URL

    def test_dotenv_used_when_shell_unset(self, tmp_path: Path):
        with patch.dict("os.environ", {}, clear=True):
            resolved = Settings(_env_file=self._dotenv(tmp_path)).database.url
        assert resolved == self.DOTENV_URL

    def test_empty_shell_export_falls_back_to_dotenv(self, tmp_path: Path):
        # env_ignore_empty=True: `FOO=` means unset, not "override with empty".
        with patch.dict("os.environ", {"DATABASE_URL": ""}):
            resolved = Settings(_env_file=self._dotenv(tmp_path)).database.url
        assert resolved == self.DOTENV_URL


class TestFlatKeysDoNotClobberNestedSiblings:
    """A flat key sets its own field, never its whole config group.

    Took prod down at v0.10.2.13: flat ``FILE_LOG_LEVEL`` rebuilt the `logging`
    group and discarded the image's nested ``LOGGING__LOG_FILE``. Driven through
    the validator because this repo's ``.env`` defines flat ``LOG_FILE``, which
    masks the bug locally by winning the very race being pinned.
    """

    NESTED_LOG_FILE = "/app/data/mixd.log"

    def _transform(self, group_data: dict[str, object]) -> dict[str, object]:
        with patch.dict("os.environ", {"FILE_LOG_LEVEL": "INFO"}):
            os.environ.pop("LOG_FILE", None)  # Fly has no .env, so it is unset
            result = Settings.transform_flat_env_vars({"logging": group_data})
        assert isinstance(result, dict)
        logging_group = result["logging"]
        assert isinstance(logging_group, dict)
        return cast("dict[str, object]", logging_group)

    def test_a_flat_key_keeps_its_nested_siblings(self):
        merged = self._transform({"log_file": self.NESTED_LOG_FILE})
        assert merged["file_level"] == "INFO"
        assert merged["log_file"] == self.NESTED_LOG_FILE

    def test_a_flat_key_still_wins_its_own_field(self):
        # Precedence for the field the flat key names is unchanged.
        merged = self._transform({"file_level": "DEBUG"})
        assert merged["file_level"] == "INFO"

    def test_a_group_with_no_nested_values_is_unaffected(self):
        merged = self._transform({})
        assert merged["file_level"] == "INFO"


class TestAppleFlatEnvRoutes:
    """Flat APPLE_* env vars route into the credentials group."""

    APPLE_ENV: ClassVar[dict[str, str]] = {
        "APPLE_TEAM_ID": "TEAM123456",
        "APPLE_KEY_ID": "KEYID12345",
        "APPLE_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----\nfakekey\n-----END PRIVATE KEY-----",
        "APPLE_MUSIC_ORIGIN": "https://mixd.example.com",
    }

    def test_apple_vars_route_to_credentials(self):
        with patch.dict("os.environ", self.APPLE_ENV):
            creds = Settings(_env_file=None).credentials
        assert creds.apple_team_id == self.APPLE_ENV["APPLE_TEAM_ID"]
        assert creds.apple_key_id == self.APPLE_ENV["APPLE_KEY_ID"]
        assert (
            creds.apple_private_key.get_secret_value()
            == self.APPLE_ENV["APPLE_PRIVATE_KEY"]
        )
        assert creds.apple_music_origin == self.APPLE_ENV["APPLE_MUSIC_ORIGIN"]


class TestTidalFlatEnvRoutes:
    """Flat TIDAL_* env vars route into the credentials group."""

    TIDAL_ENV: ClassVar[dict[str, str]] = {
        "TIDAL_CLIENT_ID": "tidal-client-123",
        "TIDAL_REDIRECT_URI": "http://localhost:5173/auth/tidal/callback",
        "TIDAL_CLI_REDIRECT_URI": "http://127.0.0.1:8899/callback",
    }

    def test_tidal_vars_route_to_credentials(self):
        with patch.dict("os.environ", self.TIDAL_ENV):
            creds = Settings(_env_file=None).credentials
        assert creds.tidal_client_id == self.TIDAL_ENV["TIDAL_CLIENT_ID"]
        assert creds.tidal_redirect_uri == self.TIDAL_ENV["TIDAL_REDIRECT_URI"]
        assert creds.tidal_cli_redirect_uri == self.TIDAL_ENV["TIDAL_CLI_REDIRECT_URI"]

    def test_cli_redirect_uri_defaults_empty(self):
        # Optional: unset means the CLI browser fallback derives its
        # listener from the primary TIDAL_REDIRECT_URI.
        with patch.dict(
            "os.environ",
            {k: v for k, v in self.TIDAL_ENV.items() if k != "TIDAL_CLI_REDIRECT_URI"},
            clear=False,
        ):
            creds = Settings(_env_file=None).credentials
        assert creds.tidal_cli_redirect_uri == ""


class TestServerConfig:
    """ServerConfig validates host/port with sensible defaults."""

    def test_defaults(self):
        config = ServerConfig()
        # Loopback: containers bind all interfaces via uvicorn's own --host flag.
        assert config.host == "127.0.0.1"
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
        events = [e["event"] for e in capture_logs]
        assert any("Spotify not configured" in e for e in events)
        assert not any("Last.fm not configured" in e for e in events)

    def test_warns_when_lastfm_unconfigured(self, capture_logs):
        creds = self._make_credentials(spotify_id="some_id")
        with patch.object(settings, "credentials", creds):
            log_startup_warnings()
        events = [e["event"] for e in capture_logs]
        assert any("Last.fm not configured" in e for e in events)
        assert not any("Spotify not configured" in e for e in events)

    def test_silent_when_all_configured(self, capture_logs):
        creds = self._make_credentials(spotify_id="some_id", lastfm_key="some_key")
        with patch.object(settings, "credentials", creds):
            log_startup_warnings()
        assert len(capture_logs) == 0

    def test_warns_when_apple_partially_configured(self, capture_logs):
        from src.config.settings import CredentialsConfig

        creds = CredentialsConfig(
            spotify_client_id="some_id",
            lastfm_key="some_key",
            apple_team_id="TEAM123456",  # key_id and private_key missing
        )
        with patch.object(settings, "credentials", creds):
            log_startup_warnings()
        events = [e["event"] for e in capture_logs]
        assert any("Apple Music partially configured" in e for e in events)

    def test_silent_when_apple_fully_configured(self, capture_logs):
        from src.config.settings import CredentialsConfig

        creds = CredentialsConfig(
            spotify_client_id="some_id",
            lastfm_key="some_key",
            apple_team_id="TEAM123456",
            apple_key_id="KEYID12345",
            apple_private_key="-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----",
        )
        with patch.object(settings, "credentials", creds):
            log_startup_warnings()
        assert len(capture_logs) == 0

    def test_silent_when_apple_fully_unconfigured(self, capture_logs):
        creds = self._make_credentials(spotify_id="some_id", lastfm_key="some_key")
        with patch.object(settings, "credentials", creds):
            log_startup_warnings()
        assert not any("Apple Music" in e["event"] for e in capture_logs)

    def test_warns_when_tidal_partially_configured(self, capture_logs):
        from src.config.settings import CredentialsConfig

        creds = CredentialsConfig(
            spotify_client_id="some_id",
            lastfm_key="some_key",
            tidal_client_id="tidal-client-123",  # redirect_uri missing
        )
        with patch.object(settings, "credentials", creds):
            log_startup_warnings()
        events = [e["event"] for e in capture_logs]
        assert any("Tidal partially configured" in e for e in events)

    def test_silent_when_tidal_fully_configured(self, capture_logs):
        from src.config.settings import CredentialsConfig

        creds = CredentialsConfig(
            spotify_client_id="some_id",
            lastfm_key="some_key",
            tidal_client_id="tidal-client-123",
            tidal_redirect_uri="http://127.0.0.1:8888/tidal/callback",
        )
        with patch.object(settings, "credentials", creds):
            log_startup_warnings()
        assert not any("Tidal" in e["event"] for e in capture_logs)

    def test_silent_when_tidal_fully_unconfigured(self, capture_logs):
        creds = self._make_credentials(spotify_id="some_id", lastfm_key="some_key")
        with patch.object(settings, "credentials", creds):
            log_startup_warnings()
        assert not any("Tidal" in e["event"] for e in capture_logs)
