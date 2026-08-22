"""Tests for CLI connector status command."""

from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from src.domain.entities.connector import ConnectorStatus
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
