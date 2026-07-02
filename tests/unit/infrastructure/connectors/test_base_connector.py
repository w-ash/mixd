"""Unit tests for BaseAPIConnector.get_playlist default behavior."""

from collections.abc import Mapping

import pytest

from src.domain.entities.track import Artist, ConnectorTrack
from src.infrastructure.connectors.base import BaseAPIConnector


class MockConnector(BaseAPIConnector):
    """Mock connector for testing BaseAPIConnector default behavior."""

    @property
    def connector_name(self) -> str:
        return "test"

    def convert_track_to_connector(self, track_data: Mapping) -> ConnectorTrack:
        """Mock implementation of abstract method."""
        return ConnectorTrack(
            connector_name=self.connector_name,
            connector_track_id=track_data.get("id", "mock_id"),
            title=track_data.get("title", "Mock Title"),
            artists=[Artist(name=track_data.get("artist", "Mock Artist"))],
        )


class TestBaseAPIConnectorDelegation:
    """Test BaseAPIConnector generic method delegation patterns."""

    async def test_get_playlist_raises_for_unsupported_connector(self):
        """Test get_playlist raises NotImplementedError for unsupported connectors."""
        connector = MockConnector()  # Generic test connector

        with pytest.raises(
            NotImplementedError,
            match="Playlist operations not supported by test connector",
        ):
            await connector.get_playlist("test_playlist_id")
