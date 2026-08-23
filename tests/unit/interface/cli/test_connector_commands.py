"""Tests for CLI connector status command."""

from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from src.domain.entities.connector import ConnectorStatus
from src.infrastructure.connectors.spotify.auth import SpotifyTokenManager
from src.interface.cli.app import app

runner = CliRunner()


class TestConnectorsStatusCommand:
    def test_shows_connector_table(self):
        statuses = [
            ConnectorStatus(
                name="spotify",
                auth_method="oauth",
                connected=True,
                account_name="testuser",
                token_expires_at=1700000000,
            ),
            ConnectorStatus(
                name="lastfm",
                auth_method="oauth",
                connected=True,
                account_name="lfmuser",
            ),
            ConnectorStatus(name="musicbrainz", auth_method="none", connected=True),
            ConnectorStatus(
                name="apple_music", auth_method="coming_soon", connected=False
            ),
        ]

        with patch(
            "src.infrastructure.connectors._shared.connector_status.get_all_connector_statuses",
            new_callable=AsyncMock,
            return_value=statuses,
        ):
            result = runner.invoke(app, ["connectors"])

            assert result.exit_code == 0
            assert "spotify" in result.output
            assert "lastfm" in result.output
            assert "Connected" in result.output
            assert "Disconnected" in result.output
            assert "testuser" in result.output

    def test_shows_reauth_required_status(self):
        statuses = [
            ConnectorStatus(
                name="spotify",
                auth_method="oauth",
                connected=True,
                auth_error="reauth_required",
            ),
        ]

        with patch(
            "src.infrastructure.connectors._shared.connector_status.get_all_connector_statuses",
            new_callable=AsyncMock,
            return_value=statuses,
        ):
            result = runner.invoke(app, ["connectors"])

            assert result.exit_code == 0
            assert "Re-connect needed (session expired)" in result.output
            assert "Error" not in result.output

    def test_shows_disconnected_status(self):
        statuses = [
            ConnectorStatus(name="spotify", auth_method="oauth", connected=False),
        ]

        with patch(
            "src.infrastructure.connectors._shared.connector_status.get_all_connector_statuses",
            new_callable=AsyncMock,
            return_value=statuses,
        ):
            result = runner.invoke(app, ["connectors"])

            assert result.exit_code == 0
            assert "Disconnected" in result.output


class TestDisconnectCommand:
    def test_browser_bridge_connector_can_disconnect(self):
        """Apple Music (browser_bridge) stores a credential — the guard must
        let it through, not reject it as credential-less."""
        storage = AsyncMock()
        with patch(
            "src.infrastructure.connectors._shared.token_storage.get_token_storage",
            return_value=storage,
        ):
            result = runner.invoke(app, ["connectors", "disconnect", "apple_music"])

        assert result.exit_code == 0
        assert "Disconnected apple_music" in result.output
        storage.delete_token.assert_awaited_once()
        assert storage.delete_token.await_args.args[0] == "apple_music"

    def test_device_code_connector_can_disconnect(self):
        """device_code stores a per-user credential (CREDENTIAL_AUTH_METHODS)
        — the next device-code connector must stay disconnectable without a
        gate edit."""
        storage = AsyncMock()
        registry = {"devbox": {"auth_method": "device_code"}}
        with (
            patch(
                "src.infrastructure.connectors.discovery.discover_connectors",
                return_value=registry,
            ),
            patch(
                "src.infrastructure.connectors._shared.token_storage.get_token_storage",
                return_value=storage,
            ),
        ):
            result = runner.invoke(app, ["connectors", "disconnect", "devbox"])

        assert result.exit_code == 0
        assert "Disconnected devbox" in result.output
        storage.delete_token.assert_awaited_once()
        assert storage.delete_token.await_args.args[0] == "devbox"

    def test_public_api_connector_cannot_disconnect(self):
        storage = AsyncMock()
        with patch(
            "src.infrastructure.connectors._shared.token_storage.get_token_storage",
            return_value=storage,
        ):
            result = runner.invoke(app, ["connectors", "disconnect", "musicbrainz"])

        assert result.exit_code != 0
        storage.delete_token.assert_not_awaited()
        assert "Traceback" not in result.output

    def test_disconnect_message_states_what_is_kept(self):
        # v0.11.2 P S4: disconnect removes credentials, not data — the CLI
        # confirmation must say so, matching the web dialog's meaning.
        storage = AsyncMock()
        with patch(
            "src.infrastructure.connectors._shared.token_storage.get_token_storage",
            return_value=storage,
        ):
            result = runner.invoke(app, ["connectors", "disconnect", "spotify"])

        assert result.exit_code == 0
        normalized_output = " ".join(result.output.split())
        assert "likes, plays, playlists, and mappings" in normalized_output
        assert "reconnect" in normalized_output.lower()


class TestAuthSpotifyCommand:
    def test_stamps_account_id_into_extra_data_at_grant_time(self):
        token_info = {
            "access_token": "access-tok",
            "refresh_token": "refresh-tok",
            "expires_at": 1_700_003_600,
            "scope": "user-library-read",
            "extra_data": {"authorized_at": 1_700_000_000},
        }
        storage = AsyncMock()

        with (
            patch.object(
                SpotifyTokenManager,
                "run_browser_auth",
                MagicMock(return_value="auth-code"),
            ),
            patch.object(
                SpotifyTokenManager,
                "exchange_code",
                AsyncMock(return_value=token_info),
            ),
            patch(
                "src.infrastructure.connectors._shared.token_storage.get_token_storage",
                return_value=storage,
            ),
            patch(
                "src.infrastructure.connectors._shared.connector_status.fetch_spotify_profile",
                AsyncMock(return_value=("Real Name", "acct-cli-123")),
            ),
            patch(
                "src.interface.cli.connector_commands.sync_play_polling_after_auth",
                AsyncMock(),
            ),
        ):
            result = runner.invoke(app, ["connectors", "auth", "spotify"])

        assert result.exit_code == 0
        storage.save_token.assert_awaited_once()
        service, _user_id, saved = storage.save_token.await_args.args
        assert service == "spotify"
        assert saved["account_name"] == "Real Name"
        assert saved["extra_data"]["account_id"] == "acct-cli-123"
        # authorized_at (stamped by exchange_code) survives the account_id merge.
        assert saved["extra_data"]["authorized_at"] == 1_700_000_000
