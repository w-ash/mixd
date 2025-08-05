"""Integration tests for LastFM import service with real repository interactions.

Tests critical service + repository integration paths:
- Checkpoint persistence and retrieval
- Track resolution with database
- Import workflow with real UnitOfWork
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.domain.entities import PlayRecord, TrackPlay
from src.infrastructure.services.lastfm_play_importer import LastfmPlayImporter


class TestLastfmImportIntegration:
    """Integration tests for LastFM import with repository layer."""

    @pytest.fixture
    async def db_session(self):
        """Real database session for integration tests."""
        from src.infrastructure.persistence.database.db_connection import get_session
        async with get_session() as session:
            yield session

    @pytest.fixture
    def unit_of_work(self, db_session):
        """Real UnitOfWork with database session."""
        from src.infrastructure.persistence.repositories.factories import (
            get_unit_of_work,
        )
        return get_unit_of_work(db_session)

    @pytest.fixture
    def lastfm_importer_with_real_repos(self, unit_of_work):
        """LastfmPlayImporter with real repositories but mocked connectors."""
        with patch('src.infrastructure.services.lastfm_play_importer.LastFMConnector') as mock_connector_class:
            mock_connector = Mock()
            mock_connector.lastfm_username = "test_user"
            mock_connector_class.return_value = mock_connector
            
            with patch('src.infrastructure.services.lastfm_play_importer.LastfmTrackResolutionService') as mock_resolution_service_class:
                mock_resolution_service = AsyncMock()
                mock_resolution_service_class.return_value = mock_resolution_service
                
                importer = LastfmPlayImporter(
                    plays_repository=unit_of_work.get_plays_repository(),
                    checkpoint_repository=unit_of_work.get_checkpoint_repository(),
                    connector_repository=unit_of_work.get_connector_repository(),
                    track_repository=unit_of_work.get_track_repository(),
                    lastfm_connector=mock_connector,
                    track_resolution_service=mock_resolution_service
                )
                
                yield importer, mock_connector, mock_resolution_service

    # INTEGRATION TEST 1: Checkpoint Persistence Cycle
    @pytest.mark.asyncio
    async def test_checkpoint_persistence_cycle(self, lastfm_importer_with_real_repos, unit_of_work):
        """Test full checkpoint save/load cycle with real repository."""
        importer, _, _ = lastfm_importer_with_real_repos
        
        # Cleanup: Remove any existing checkpoint from previous test runs
        existing_checkpoint = await importer._resolve_checkpoint(username="integration_test_user", uow=unit_of_work)
        if existing_checkpoint and existing_checkpoint.id:
            checkpoint_repo = unit_of_work.get_checkpoint_repository()
            await checkpoint_repo.hard_delete(existing_checkpoint.id)
            await unit_of_work.commit()
        
        # Test 1: No existing checkpoint
        checkpoint = await importer._resolve_checkpoint(username="integration_test_user", uow=unit_of_work)
        assert checkpoint is None
        
        # Test 2: Save checkpoint
        await importer._save_day_checkpoint(
            username="integration_test_user",
            completed_date=datetime(2024, 3, 15).date(),
            day_end=datetime(2024, 3, 15, 23, 59, 59, tzinfo=UTC),
            uow=unit_of_work
        )
        
        # Test 3: Retrieve saved checkpoint
        checkpoint = await importer._resolve_checkpoint(username="integration_test_user", uow=unit_of_work)
        assert checkpoint is not None
        assert checkpoint.user_id == "integration_test_user"
        assert checkpoint.service == "lastfm"
        assert checkpoint.entity_type == "plays"
        assert checkpoint.cursor == "2024-03-15"

    # INTEGRATION TEST 2: Import Workflow with Track Resolution
    @pytest.mark.asyncio
    async def test_import_workflow_with_track_resolution(self, lastfm_importer_with_real_repos, unit_of_work):
        """Test complete import workflow with track resolution service."""
        importer, mock_connector, mock_resolution_service = lastfm_importer_with_real_repos
        
        # Arrange - Mock external API responses
        play_records = [
            PlayRecord(
                track_name="Bohemian Rhapsody",
                artist_name="Queen",
                album_name="A Night at the Opera",
                played_at=datetime(2024, 3, 15, 12, 0, tzinfo=UTC),
                service="lastfm",
                service_metadata={"mbid": "test-mbid-123"}
            )
        ]
        
        # Mock track resolution service to return resolved tracks
        from src.domain.entities.track import Artist, Track
        resolved_track = Track(
            id="track-123",
            title="Bohemian Rhapsody",
            artists=[Artist(name="Queen")],
            duration_ms=355000
        )
        mock_resolution_service.resolve_plays_to_canonical_tracks.return_value = (
            [resolved_track], 
            {"new_tracks_count": 1, "updated_tracks_count": 0}
        )
        
        # Act - Process the data
        track_plays = await importer._process_data(
            raw_data=play_records,
            batch_id="integration-test-batch",
            import_timestamp=datetime.now(UTC),
            uow=unit_of_work
        )
        
        # Assert - Verify track resolution was called and TrackPlay objects created
        assert len(track_plays) == 1
        track_play = track_plays[0]
        assert isinstance(track_play, TrackPlay)
        assert track_play.track_id == "track-123"
        assert track_play.service == "lastfm"
        assert track_play.context["track_name"] == "Bohemian Rhapsody"
        assert track_play.context["resolution_method"] == "lastfm_track_resolution_service"
        
        mock_resolution_service.resolve_plays_to_canonical_tracks.assert_called_once_with(
            play_records=play_records, 
            uow=unit_of_work
        )

    # INTEGRATION TEST 3: Error Handling with Real Repositories
    @pytest.mark.asyncio  
    async def test_checkpoint_error_handling(self, lastfm_importer_with_real_repos, unit_of_work):
        """Test checkpoint operations handle repository errors gracefully."""
        importer, _, _ = lastfm_importer_with_real_repos
        
        # Test checkpoint resolution handles missing user gracefully
        checkpoint = await importer._resolve_checkpoint(username="", uow=unit_of_work)
        assert checkpoint is None
        
        # Test checkpoint saving doesn't crash on repository errors
        with patch.object(importer.checkpoint_repository, 'save_sync_checkpoint', side_effect=Exception("DB Error")):
            # Should not raise exception, just log warning
            await importer._save_day_checkpoint(
                username="error_test_user",
                completed_date=datetime(2024, 3, 15).date(),
                day_end=datetime(2024, 3, 15, 23, 59, 59, tzinfo=UTC),
                uow=unit_of_work
            )

    # INTEGRATION TEST 4: Boundary Condition - Large Date Range
    @pytest.mark.asyncio
    async def test_date_range_boundaries_with_real_checkpoint(self, lastfm_importer_with_real_repos, unit_of_work):
        """Test date range logic with real checkpoint data."""
        importer, _, _ = lastfm_importer_with_real_repos
        
        # Save a checkpoint first
        await importer._save_day_checkpoint(
            username="boundary_test_user",
            completed_date=datetime(2024, 1, 15).date(),
            day_end=datetime(2024, 1, 15, 23, 59, 59, tzinfo=UTC),
            uow=unit_of_work
        )
        
        # Retrieve and test date range logic
        checkpoint = await importer._resolve_checkpoint(username="boundary_test_user", uow=unit_of_work)
        
        # Test incremental import respects checkpoint boundary
        start, end = importer._determine_date_range(None, None, checkpoint)
        
        # Should start from beginning of checkpoint day, end at now
        expected_start = datetime(2024, 1, 15, 0, 0, 0, tzinfo=UTC)
        assert start == expected_start
        assert end.date() == datetime.now(UTC).date()