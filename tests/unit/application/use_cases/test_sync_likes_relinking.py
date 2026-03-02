"""Tests for Spotify relinking-aware dedup in likes import.

Verifies that ImportSpotifyLikesUseCase correctly detects existing tracks
when the Spotify ID has been relinked (linked_from_id in raw_metadata).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.use_cases.sync_likes import (
    ImportSpotifyLikesCommand,
    ImportSpotifyLikesUseCase,
)
from src.domain.entities import SyncCheckpoint
from tests.fixtures import make_connector_track, make_track
from tests.fixtures.mocks import make_mock_uow


@pytest.fixture
def mock_uow():
    """Mock UnitOfWork with all required repositories."""
    uow = make_mock_uow()

    # Checkpoint repo
    checkpoint = SyncCheckpoint(user_id="test", service="spotify", entity_type="likes")
    checkpoint_repo = uow.get_checkpoint_repository()
    checkpoint_repo.get_sync_checkpoint.return_value = checkpoint
    checkpoint_repo.save_sync_checkpoint.return_value = checkpoint

    # Service connector provider (Spotify)
    mock_spotify = AsyncMock()
    mock_spotify.get_liked_tracks.return_value = ([], None)
    provider = MagicMock()
    provider.get_connector.return_value = mock_spotify
    uow.get_service_connector_provider = MagicMock(return_value=provider)

    return uow


class TestSyncLikesRelinking:
    """Tests for relinking-aware dedup in Spotify likes import."""

    async def test_relinked_track_found_via_alternate_id(self, mock_uow):
        """Track liked under ID 'B' but exists under original ID 'A' → found, not re-created."""
        # Spotify returns track with new ID "B", linked_from original "A"
        connector_track = make_connector_track("new_B", linked_from_id="original_A")
        existing_track = make_track(
            1, connector_track_identifiers={"spotify": "original_A"}
        )

        spotify = mock_uow.get_service_connector_provider().get_connector()
        spotify.get_liked_tracks.side_effect = [([connector_track], None)]

        connector_repo = mock_uow.get_connector_repository()
        # Primary ID "new_B" NOT found, but alternate "original_A" IS found
        connector_repo.find_tracks_by_connectors.return_value = {
            ("spotify", "original_A"): existing_track,
        }

        like_repo = mock_uow.get_like_repository()
        # Track needs likes (not yet synced)
        like_repo.get_liked_status_batch.return_value = {1: {"spotify": False}}
        like_repo.save_track_likes_batch.return_value = []

        command = ImportSpotifyLikesCommand(user_id="test_user")
        result = await ImportSpotifyLikesUseCase().execute(command, mock_uow)

        # Should NOT have called ingest (track already exists)
        connector_repo.ingest_external_tracks_bulk.assert_not_called()

        # Should have imported (liked) the existing track
        imported = next(
            m for m in result.summary_metrics.metrics if m.name == "imported"
        )
        assert imported.value == 1

        # Verify the alternate ID was included in the lookup
        find_call = connector_repo.find_tracks_by_connectors.call_args[0][0]
        assert ("spotify", "original_A") in find_call
        assert ("spotify", "new_B") in find_call

    async def test_new_relinked_track_ingested_normally(self, mock_uow):
        """Brand new track with linked_from_id → created as usual (not found under either ID)."""
        connector_track = make_connector_track("new_B", linked_from_id="original_A")
        new_track = make_track(1, connector_track_identifiers={"spotify": "new_B"})

        spotify = mock_uow.get_service_connector_provider().get_connector()
        spotify.get_liked_tracks.side_effect = [([connector_track], None)]

        connector_repo = mock_uow.get_connector_repository()
        # Neither ID found
        connector_repo.find_tracks_by_connectors.return_value = {}
        connector_repo.ingest_external_tracks_bulk.return_value = [new_track]

        like_repo = mock_uow.get_like_repository()
        like_repo.save_track_likes_batch.return_value = []

        command = ImportSpotifyLikesCommand(user_id="test_user")
        result = await ImportSpotifyLikesUseCase().execute(command, mock_uow)

        # Should have been ingested as new
        connector_repo.ingest_external_tracks_bulk.assert_called_once()
        imported = next(
            m for m in result.summary_metrics.metrics if m.name == "imported"
        )
        assert imported.value == 1

    async def test_no_alternate_lookup_without_linked_from(self, mock_uow):
        """Normal tracks without linked_from_id use standard lookup only."""
        connector_track = make_connector_track("normal_id")

        spotify = mock_uow.get_service_connector_provider().get_connector()
        spotify.get_liked_tracks.side_effect = [([connector_track], None)]

        connector_repo = mock_uow.get_connector_repository()
        connector_repo.find_tracks_by_connectors.return_value = {}
        connector_repo.ingest_external_tracks_bulk.return_value = [
            make_track(1, connector_track_identifiers={"spotify": "normal_id"})
        ]

        like_repo = mock_uow.get_like_repository()
        like_repo.save_track_likes_batch.return_value = []

        command = ImportSpotifyLikesCommand(user_id="test_user")
        await ImportSpotifyLikesUseCase().execute(command, mock_uow)

        # Only the primary ID should be in the lookup
        find_call = connector_repo.find_tracks_by_connectors.call_args[0][0]
        assert find_call == [("spotify", "normal_id")]
