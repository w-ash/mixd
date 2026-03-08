"""Tests for CLI connector status command."""

from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from src.infrastructure.connectors._shared.connector_status import ConnectorStatus
from src.interface.cli.app import app

runner = CliRunner()


class TestConnectorsStatusCommand:
    def test_shows_connector_table(self):
        statuses = [
            ConnectorStatus(
                name="spotify",
                connected=True,
                account_name="testuser",
                token_expires_at=1700000000,
            ),
            ConnectorStatus(name="lastfm", connected=True, account_name="lfmuser"),
            ConnectorStatus(name="musicbrainz", connected=True),
            ConnectorStatus(name="apple", connected=False),
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

    def test_shows_disconnected_status(self):
        statuses = [
            ConnectorStatus(name="spotify", connected=False),
        ]

        with patch(
            "src.infrastructure.connectors._shared.connector_status.get_all_connector_statuses",
            new_callable=AsyncMock,
            return_value=statuses,
        ):
            result = runner.invoke(app, ["connectors"])

            assert result.exit_code == 0
            assert "Disconnected" in result.output
