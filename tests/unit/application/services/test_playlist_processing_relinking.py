"""Tests for Spotify relinking-aware dedup in playlist processing.

Verifies that ConnectorPlaylistProcessingService correctly detects existing
tracks when the Spotify ID has been relinked (linked_from_id in raw_metadata).
"""

from unittest.mock import MagicMock

import pytest

from src.application.services.connector_playlist_processing_service import (
    ConnectorPlaylistProcessingService,
)
from src.domain.entities.playlist import ConnectorPlaylistItem
from src.domain.entities.track import Artist, Track
from tests.fixtures import make_connector_playlist, make_connector_track, make_track
from tests.fixtures.mocks import make_mock_uow


def _make_playlist_item(
    identifier: str,
    position: int = 0,
    track_data: dict | None = None,
) -> ConnectorPlaylistItem:
    """Create a ConnectorPlaylistItem with full_track_data in extras."""
    base_data = track_data or {
        "id": identifier,
        "name": f"Song {identifier}",
        "artists": [{"name": "Artist"}],
    }
    return ConnectorPlaylistItem(
        connector_track_identifier=identifier,
        position=position,
        extras={"full_track_data": base_data},
    )


def _make_playlist_item_with_linked_from(
    response_id: str,
    linked_from_id: str,
    position: int = 0,
) -> ConnectorPlaylistItem:
    """Create a ConnectorPlaylistItem whose track data includes linked_from."""
    return ConnectorPlaylistItem(
        connector_track_identifier=response_id,
        position=position,
        extras={
            "full_track_data": {
                "id": response_id,
                "name": f"Song {response_id}",
                "artists": [{"name": "Artist"}],
                "linked_from": {"id": linked_from_id},
            }
        },
    )


@pytest.fixture
def mock_uow():
    """Mock UnitOfWork with connector repos and provider."""
    uow = make_mock_uow()

    # Service connector provider — returns a connector that converts tracks
    mock_connector = MagicMock()
    mock_connector.convert_track_to_connector = MagicMock(
        side_effect=lambda d: make_connector_track(
            d["id"],
            linked_from_id=d.get("linked_from", {}).get("id")
            if d.get("linked_from")
            else None,
        )
    )
    provider = MagicMock()
    provider.get_connector.return_value = mock_connector
    uow.get_service_connector_provider = MagicMock(return_value=provider)

    return uow


class TestPlaylistProcessingRelinking:
    """Tests for relinking-aware dedup in playlist processing."""

    async def test_playlist_relinked_track_found_via_alternate(self, mock_uow):
        """Playlist has ID 'B' (relinked from 'A'), DB has 'A' → found, not re-created."""
        item = _make_playlist_item_with_linked_from("new_B", "original_A")
        playlist = make_connector_playlist([item])

        existing_track = make_track(
            1, connector_track_identifiers={"spotify": "original_A"}
        )

        connector_repo = mock_uow.get_connector_repository()
        # Primary lookup for "new_B" returns nothing, but alternate "original_A" hits
        connector_repo.find_tracks_by_connectors.return_value = {
            ("spotify", "original_A"): existing_track,
        }

        service = ConnectorPlaylistProcessingService()
        result = await service.process_connector_playlist(playlist, mock_uow)

        # Track found via alternate → no ingestion needed
        connector_repo.ingest_external_tracks_bulk.assert_not_called()

        # Playlist should have 1 entry (the found track)
        assert len(result.entries) == 1
        assert result.entries[0].track.id == 1

        # Verify the alternate ID was included in the lookup
        find_call = connector_repo.find_tracks_by_connectors.call_args[0][0]
        assert ("spotify", "original_A") in find_call
        assert ("spotify", "new_B") in find_call

    async def test_non_spotify_connector_ignores_linked_from(self, mock_uow):
        """Last.fm playlist doesn't attempt alternate lookup even if linked_from_id present."""
        # Create a lastfm connector mock
        mock_connector = MagicMock()
        mock_connector.convert_track_to_connector = MagicMock(
            side_effect=lambda d: make_connector_track(
                d["id"],
                linked_from_id="some_alt",
                connector_name="lastfm",
            )
        )
        provider = mock_uow.get_service_connector_provider()
        provider.get_connector.return_value = mock_connector

        item = ConnectorPlaylistItem(
            connector_track_identifier="lfm_123",
            position=0,
            extras={
                "full_track_data": {
                    "id": "lfm_123",
                    "name": "Song",
                    "artists": [{"name": "A"}],
                }
            },
        )
        playlist = make_connector_playlist([item], connector_name="lastfm")

        connector_repo = mock_uow.get_connector_repository()
        connector_repo.find_tracks_by_connectors.return_value = {}
        new_track = Track(
            id=1,
            title="Song",
            artists=[Artist(name="A")],
            connector_track_identifiers={"lastfm": "lfm_123"},
        )
        connector_repo.ingest_external_tracks_bulk.return_value = [new_track]

        service = ConnectorPlaylistProcessingService()
        result = await service.process_connector_playlist(playlist, mock_uow)

        # Only the primary ID should be in the lookup (no alternates for lastfm)
        find_call = connector_repo.find_tracks_by_connectors.call_args[0][0]
        assert find_call == [("lastfm", "lfm_123")]

    async def test_normal_track_no_alternate_lookup(self, mock_uow):
        """Normal Spotify track (no linked_from) uses standard lookup only."""
        item = _make_playlist_item("normal_id")
        playlist = make_connector_playlist([item])

        connector_repo = mock_uow.get_connector_repository()
        connector_repo.find_tracks_by_connectors.return_value = {}
        new_track = make_track(1, connector_track_identifiers={"spotify": "normal_id"})
        connector_repo.ingest_external_tracks_bulk.return_value = [new_track]

        service = ConnectorPlaylistProcessingService()
        await service.process_connector_playlist(playlist, mock_uow)

        find_call = connector_repo.find_tracks_by_connectors.call_args[0][0]
        assert find_call == [("spotify", "normal_id")]
