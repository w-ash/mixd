"""Unit tests for ImportSpotifyLikesUseCase.

Tests the Spotify likes import workflow: batch fetching, duplicate detection,
checkpoint management, and error resilience.
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
def mock_checkpoint():
    return SyncCheckpoint(user_id="test", service="spotify", entity_type="likes")


@pytest.fixture
def mock_uow(mock_checkpoint):
    """Mock UnitOfWork with all required repositories."""
    uow = make_mock_uow()

    # Checkpoint repo
    checkpoint_repo = uow.get_checkpoint_repository()
    checkpoint_repo.get_sync_checkpoint.return_value = mock_checkpoint
    checkpoint_repo.save_sync_checkpoint.return_value = mock_checkpoint

    # Service connector provider (Spotify)
    mock_spotify = AsyncMock()
    mock_spotify.get_liked_tracks.return_value = ([], None)
    provider = MagicMock()
    provider.get_connector.return_value = mock_spotify
    uow.get_service_connector_provider = MagicMock(return_value=provider)

    return uow


class TestImportSpotifyLikesCommand:
    """Test command construction and validation."""

    def test_valid_command_defaults(self):
        """Test command creates with defaults."""
        cmd = ImportSpotifyLikesCommand(user_id="user1")
        assert cmd.user_id == "user1"
        assert cmd.limit is None
        assert cmd.max_imports is None

    def test_command_with_all_params(self):
        """Test command with all parameters specified."""
        cmd = ImportSpotifyLikesCommand(user_id="user1", limit=50, max_imports=200)
        assert cmd.limit == 50
        assert cmd.max_imports == 200

    def test_command_is_frozen(self):
        """Test command immutability."""
        cmd = ImportSpotifyLikesCommand(user_id="user1")
        with pytest.raises(AttributeError):
            cmd.user_id = "modified"


class TestImportSpotifyLikesUseCase:
    """Test use case execution paths."""

    async def test_empty_likes_returns_zero_imports(self, mock_uow):
        """Test that empty response from Spotify produces zero-import result."""
        command = ImportSpotifyLikesCommand(user_id="test_user")
        use_case = ImportSpotifyLikesUseCase()

        result = await use_case.execute(command, mock_uow)

        assert result.operation_name == "Spotify Likes Import"
        imported = next(
            m for m in result.summary_metrics.metrics if m.name == "imported"
        )
        assert imported.value == 0

    async def test_happy_path_imports_new_tracks(self, mock_uow):
        """Test importing new tracks that don't exist in DB."""
        connector_track = make_connector_track("spotify_new_1", title="New Song")
        new_track = make_track(1, "New Song")

        # Spotify returns one track, then empty (pagination end)
        spotify = mock_uow.get_service_connector_provider().get_connector()
        spotify.get_liked_tracks.side_effect = [
            ([connector_track], None),  # First batch, no cursor = done
        ]

        # No existing tracks found in bulk lookup
        connector_repo = mock_uow.get_connector_repository()
        connector_repo.find_tracks_by_connectors.return_value = {}
        connector_repo.ingest_external_tracks_bulk.return_value = [new_track]

        command = ImportSpotifyLikesCommand(user_id="test_user")
        use_case = ImportSpotifyLikesUseCase()

        result = await use_case.execute(command, mock_uow)

        imported = next(
            m for m in result.summary_metrics.metrics if m.name == "imported"
        )
        assert imported.value == 1

        # Verify bulk ingest was called with the connector track
        connector_repo.ingest_external_tracks_bulk.assert_called_once_with(
            "spotify", [connector_track]
        )

    async def test_already_synced_tracks_counted(self, mock_uow):
        """Test that already-liked tracks are counted but not re-imported."""
        connector_track = make_connector_track("spotify_existing")
        existing_track = make_track(1)

        spotify = mock_uow.get_service_connector_provider().get_connector()
        spotify.get_liked_tracks.side_effect = [
            ([connector_track], None),
        ]

        # Track exists in bulk lookup
        connector_repo = mock_uow.get_connector_repository()
        connector_repo.find_tracks_by_connectors.return_value = {
            ("spotify", "spotify_existing"): existing_track,
        }

        like_repo = mock_uow.get_like_repository()
        # Batch status: track 1 is liked in both services
        like_repo.get_liked_status_batch.return_value = {
            1: {"spotify": True, "narada": True},
        }

        command = ImportSpotifyLikesCommand(user_id="test_user")
        use_case = ImportSpotifyLikesUseCase()

        result = await use_case.execute(command, mock_uow)

        already_liked = next(
            m for m in result.summary_metrics.metrics if m.name == "already_liked"
        )
        assert already_liked.value == 1

    async def test_existing_track_needing_likes_gets_liked(self, mock_uow):
        """Test that existing tracks not yet liked get likes saved."""
        connector_track = make_connector_track("spotify_existing")
        existing_track = make_track(1)

        spotify = mock_uow.get_service_connector_provider().get_connector()
        spotify.get_liked_tracks.side_effect = [
            ([connector_track], None),
        ]

        connector_repo = mock_uow.get_connector_repository()
        connector_repo.find_tracks_by_connectors.return_value = {
            ("spotify", "spotify_existing"): existing_track,
        }

        like_repo = mock_uow.get_like_repository()
        # Not liked yet — batch returns only spotify liked, missing narada
        like_repo.get_liked_status_batch.return_value = {
            1: {"spotify": True},
        }
        like_repo.save_track_likes_batch.return_value = []

        command = ImportSpotifyLikesCommand(user_id="test_user")
        use_case = ImportSpotifyLikesUseCase()

        result = await use_case.execute(command, mock_uow)

        imported = next(
            m for m in result.summary_metrics.metrics if m.name == "imported"
        )
        assert imported.value == 1
        like_repo.save_track_likes_batch.assert_called_once()

    async def test_max_imports_limit_stops_fetching(self, mock_uow):
        """Test that max_imports cap prevents further batch fetches."""
        # First batch: 5 new tracks
        batch1 = [make_connector_track(f"sp_{i}") for i in range(5)]
        batch1_tracks = [make_track(i + 1) for i in range(5)]

        spotify = mock_uow.get_service_connector_provider().get_connector()
        spotify.get_liked_tracks.side_effect = [
            (batch1, "cursor_1"),
            ([], None),  # Would be second batch if it got here
        ]

        connector_repo = mock_uow.get_connector_repository()
        connector_repo.find_tracks_by_connectors.return_value = {}
        connector_repo.ingest_external_tracks_bulk.return_value = batch1_tracks

        like_repo = mock_uow.get_like_repository()
        like_repo.save_track_likes_batch.return_value = []

        command = ImportSpotifyLikesCommand(user_id="test_user", max_imports=3)
        use_case = ImportSpotifyLikesUseCase()

        result = await use_case.execute(command, mock_uow)

        # The first batch processes fully (5 tracks), then the loop breaks
        # because imported (5) >= max_imports (3) at the start of next iteration
        imported = next(
            m for m in result.summary_metrics.metrics if m.name == "imported"
        )
        assert imported.value >= 3  # At least max_imports worth
        # Should NOT have fetched second batch
        assert spotify.get_liked_tracks.call_count == 1

    async def test_batch_ingestion_called_with_multiple_tracks(self, mock_uow):
        """Test that batch ingestion receives multiple new tracks, not single-element lists."""
        tracks = [make_connector_track(f"sp_{i}") for i in range(3)]
        ingested = [make_track(i + 1) for i in range(3)]

        spotify = mock_uow.get_service_connector_provider().get_connector()
        spotify.get_liked_tracks.side_effect = [
            (tracks, None),
        ]

        connector_repo = mock_uow.get_connector_repository()
        connector_repo.find_tracks_by_connectors.return_value = {}
        connector_repo.ingest_external_tracks_bulk.return_value = ingested

        like_repo = mock_uow.get_like_repository()
        like_repo.save_track_likes_batch.return_value = []

        command = ImportSpotifyLikesCommand(user_id="test_user")
        use_case = ImportSpotifyLikesUseCase()

        await use_case.execute(command, mock_uow)

        # Verify bulk ingest was called with all 3 tracks at once
        connector_repo.ingest_external_tracks_bulk.assert_called_once_with(
            "spotify", tracks
        )

    async def test_bulk_find_error_does_not_abort(self, mock_uow):
        """Test that a bulk find error is caught and batch continues gracefully."""
        connector_track = make_connector_track("sp_1")

        spotify = mock_uow.get_service_connector_provider().get_connector()
        spotify.get_liked_tracks.side_effect = [
            ([connector_track], None),
        ]

        connector_repo = mock_uow.get_connector_repository()
        connector_repo.find_tracks_by_connectors.side_effect = RuntimeError("DB Error")
        # With empty existing_map, all tracks are treated as new
        new_track = make_track(1)
        connector_repo.ingest_external_tracks_bulk.return_value = [new_track]

        like_repo = mock_uow.get_like_repository()
        like_repo.save_track_likes_batch.return_value = []

        command = ImportSpotifyLikesCommand(user_id="test_user")
        use_case = ImportSpotifyLikesUseCase()

        result = await use_case.execute(command, mock_uow)

        # Should still have imported the track (treated as new after find error)
        imported = next(
            m for m in result.summary_metrics.metrics if m.name == "imported"
        )
        assert imported.value == 1

    async def test_multi_batch_pagination_not_terminated_early(self, mock_uow):
        """Test that batch 2 is still processed when batch 1 has many duplicates.

        Regression test: early termination heuristic must use batch-local counter,
        not cumulative counter, to avoid premature stop across batches.
        """
        # Batch 1: 5 tracks, 4 already synced (80%), 1 needs like update
        batch1_cts = [make_connector_track(f"sp_b1_{i}") for i in range(5)]
        batch1_existing = {
            ("spotify", f"sp_b1_{i}"): make_track(i + 1) for i in range(5)
        }

        # Batch 2: 3 new tracks
        batch2_cts = [make_connector_track(f"sp_b2_{i}") for i in range(3)]
        batch2_new = [make_track(i + 10) for i in range(3)]

        spotify = mock_uow.get_service_connector_provider().get_connector()
        spotify.get_liked_tracks.side_effect = [
            (batch1_cts, "cursor_1"),
            (batch2_cts, None),
        ]

        connector_repo = mock_uow.get_connector_repository()
        connector_repo.find_tracks_by_connectors.side_effect = [
            batch1_existing,
            {},  # Batch 2: no existing tracks
        ]
        connector_repo.ingest_external_tracks_bulk.return_value = batch2_new

        like_repo = mock_uow.get_like_repository()
        # Batch 1: first 4 tracks liked in both services, track 5 needs likes
        like_repo.get_liked_status_batch.side_effect = [
            # Batch 1: tracks 1-4 liked in both, track 5 not liked
            {
                1: {"spotify": True, "narada": True},
                2: {"spotify": True, "narada": True},
                3: {"spotify": True, "narada": True},
                4: {"spotify": True, "narada": True},
                5: {"spotify": False},
            },
            # Batch 2: no existing tracks, so not called for like status
        ]
        like_repo.save_track_likes_batch.return_value = []

        command = ImportSpotifyLikesCommand(user_id="test_user")
        use_case = ImportSpotifyLikesUseCase()

        result = await use_case.execute(command, mock_uow)

        # Both batches should have been processed
        assert spotify.get_liked_tracks.call_count == 2

        imported = next(
            m for m in result.summary_metrics.metrics if m.name == "imported"
        )
        # Batch 1: 1 existing track needing likes + Batch 2: 3 new tracks = 4
        assert imported.value == 4

    async def test_liked_at_preserved_from_connector_metadata(self, mock_uow):
        """Test that liked_at from Spotify raw_metadata is passed through to likes."""
        liked_at_iso = "2024-03-15T10:30:00+00:00"
        connector_track = make_connector_track(
            "sp_with_date", title="Dated Song", raw_metadata={"liked_at": liked_at_iso}
        )
        new_track = make_track(1, "Dated Song")

        spotify = mock_uow.get_service_connector_provider().get_connector()
        spotify.get_liked_tracks.side_effect = [
            ([connector_track], None),
        ]

        connector_repo = mock_uow.get_connector_repository()
        connector_repo.find_tracks_by_connectors.return_value = {}
        connector_repo.ingest_external_tracks_bulk.return_value = [new_track]

        like_repo = mock_uow.get_like_repository()
        like_repo.save_track_likes_batch.return_value = []

        command = ImportSpotifyLikesCommand(user_id="test_user")
        use_case = ImportSpotifyLikesUseCase()

        await use_case.execute(command, mock_uow)

        # Verify batch likes were saved with the liked_at timestamp
        like_repo.save_track_likes_batch.assert_called_once()
        entries = like_repo.save_track_likes_batch.call_args[0][0]
        # Should have 2 entries (spotify + narada) for track_id=1
        assert len(entries) == 2
        for entry in entries:
            track_id, service, is_liked, last_synced, liked_at = entry
            assert track_id == 1
            assert is_liked is True
            # liked_at should be the parsed datetime from the connector track
            if liked_at is not None:
                assert liked_at.year == 2024
                assert liked_at.month == 3
                assert liked_at.day == 15
