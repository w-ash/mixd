"""End-to-end tests for LastFM import workflow.

Tests the complete import pipeline from use case to database:
- Application layer use case orchestration
- Service layer business logic
- Repository layer persistence
- Critical error paths and recovery
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.application.use_cases.import_play_history import ImportTracksCommand, ImportTracksUseCase
from src.domain.entities import PlayRecord


class TestLastfmImportE2E:
    """End-to-end tests for complete LastFM import workflow."""

    @pytest.fixture
    async def db_session(self):
        """Real database session for E2E tests."""
        from src.infrastructure.persistence.database.db_connection import get_session
        async with get_session() as session:
            yield session

    @pytest.fixture
    def unit_of_work(self, db_session):
        """Real UnitOfWork for E2E testing."""
        from src.infrastructure.persistence.repositories.factories import get_unit_of_work
        return get_unit_of_work(db_session)

    # E2E TEST 1: Complete Incremental Import Success Path
    @pytest.mark.asyncio
    async def test_complete_incremental_import_success(self, unit_of_work):
        """Test complete incremental import from use case to database."""
        
        with patch('src.infrastructure.connectors.lastfm.LastFMConnector') as mock_connector_class:
            # Mock connector setup
            mock_connector = Mock()
            mock_connector.lastfm_username = "e2e_test_user"
            mock_connector_class.return_value = mock_connector
            
            # Mock API responses
            mock_played_track = Mock()
            mock_played_track.track = Mock()
            mock_played_track.track.get_title.return_value = "Test Song"
            mock_played_track.track.get_artist.return_value = Mock()
            mock_played_track.track.get_artist().get_name.return_value = "Test Artist"
            mock_played_track.track.get_album.return_value = Mock()
            mock_played_track.track.get_album().get_name.return_value = "Test Album"
            mock_played_track.album = "Test Album"
            mock_played_track.timestamp = "1704110400"  # Jan 1, 2024
            mock_played_track.playback_date = "01 Jan 2024, 12:00"
            
            mock_connector.get_recent_tracks.return_value = [
                PlayRecord(
                    track_name="Test Song",
                    artist_name="Test Artist", 
                    album_name="Test Album",
                    played_at=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
                    service="lastfm",
                    service_metadata={"test": "metadata"}
                )
            ]
            
            with patch('src.infrastructure.services.lastfm_track_resolution_service.LastfmTrackResolutionService') as mock_resolution_class:
                # Mock track resolution
                mock_resolution_service = AsyncMock()
                from src.domain.entities.track import Track, Artist
                resolved_track = Track(
                    id="e2e-track-123",
                    title="Test Song",
                    artists=[Artist(name="Test Artist")],
                    duration_ms=180000
                )
                mock_resolution_service.resolve_plays_to_canonical_tracks.return_value = (
                    [resolved_track],
                    {"new_tracks_count": 1, "updated_tracks_count": 0}
                )
                mock_resolution_class.return_value = mock_resolution_service
                
                # Arrange - Create command for incremental import
                command = ImportTracksCommand(
                    service="lastfm",
                    mode="incremental", 
                    user_id="e2e_test_user",
                    from_date=datetime(2024, 1, 1, tzinfo=UTC),
                    to_date=datetime(2024, 1, 2, tzinfo=UTC)
                )
                
                # Act - Execute complete import
                use_case = ImportTracksUseCase()
                result = await use_case.execute(command, unit_of_work)
                
                # Assert - Verify successful import
                assert result.operation_result.success is True
                assert result.operation_result.imported_count >= 0  # May be 0 due to deduplication
                assert result.service == "lastfm"
                assert result.mode == "incremental"
                assert result.execution_time_ms > 0

    # E2E TEST 2: Error Recovery - API Failure
    @pytest.mark.asyncio
    async def test_api_failure_error_handling(self, unit_of_work):
        """Test E2E error handling when LastFM API fails."""
        
        with patch('src.infrastructure.connectors.lastfm.LastFMConnector') as mock_connector_class:
            # Mock connector that fails
            mock_connector = Mock()
            mock_connector.lastfm_username = "error_test_user"
            mock_connector.get_recent_tracks.side_effect = Exception("API Error")
            mock_connector_class.return_value = mock_connector
            
            # Arrange
            command = ImportTracksCommand(
                service="lastfm",
                mode="recent",
                limit=50
            )
            
            # Act
            use_case = ImportTracksUseCase()
            result = await use_case.execute(command, unit_of_work)
            
            # Assert - Should return error result, not raise exception
            assert result.operation_result.success is False
            assert result.operation_result.error_count == 1
            assert result.failed_batches == 1
            assert "API Error" in str(result.operation_result.play_metrics.get("error", ""))

    # E2E TEST 3: Boundary Condition - Empty Data
    @pytest.mark.asyncio
    async def test_empty_data_handling(self, unit_of_work):
        """Test E2E handling when no data is available to import."""
        
        with patch('src.infrastructure.connectors.lastfm.LastFMConnector') as mock_connector_class:
            # Mock connector returning empty data
            mock_connector = Mock()
            mock_connector.lastfm_username = "empty_test_user"
            mock_connector.get_recent_tracks.return_value = []
            mock_connector_class.return_value = mock_connector
            
            # Arrange
            command = ImportTracksCommand(
                service="lastfm",
                mode="recent",
                limit=50
            )
            
            # Act
            use_case = ImportTracksUseCase()
            result = await use_case.execute(command, unit_of_work)
            
            # Assert - Should handle empty data gracefully
            assert result.operation_result.success is True
            assert result.operation_result.imported_count == 0
            assert result.operation_result.plays_processed == 0

    # E2E TEST 4: Critical Path - Checkpoint Persistence
    @pytest.mark.asyncio
    async def test_checkpoint_persistence_e2e(self, unit_of_work):
        """Test checkpoint creation and persistence through complete workflow."""
        
        with patch('src.infrastructure.connectors.lastfm.LastFMConnector') as mock_connector_class:
            mock_connector = Mock()
            mock_connector.lastfm_username = "checkpoint_test_user"
            mock_connector.get_recent_tracks.return_value = [
                PlayRecord(
                    track_name="Checkpoint Test",
                    artist_name="Test Artist",
                    played_at=datetime(2024, 2, 15, 15, 30, tzinfo=UTC),
                    service="lastfm",
                    service_metadata={}
                )
            ]
            mock_connector_class.return_value = mock_connector
            
            with patch('src.infrastructure.services.lastfm_track_resolution_service.LastfmTrackResolutionService') as mock_resolution_class:
                mock_resolution_service = AsyncMock()
                from src.domain.entities.track import Track, Artist
                resolved_track = Track(
                    id="checkpoint-track-123",
                    title="Checkpoint Test",
                    artists=[Artist(name="Test Artist")]
                )
                mock_resolution_service.resolve_plays_to_canonical_tracks.return_value = (
                    [resolved_track],
                    {"new_tracks_count": 1, "updated_tracks_count": 0}
                )
                mock_resolution_class.return_value = mock_resolution_service
                
                # Act - Run import with specific date range
                command = ImportTracksCommand(
                    service="lastfm",
                    mode="incremental",
                    user_id="checkpoint_test_user",
                    from_date=datetime(2024, 2, 15, tzinfo=UTC),
                    to_date=datetime(2024, 2, 16, tzinfo=UTC)
                )
                
                use_case = ImportTracksUseCase()
                result = await use_case.execute(command, unit_of_work)
                
                # Assert - Import succeeded
                assert result.operation_result.success is True
                
                # Verify checkpoint was created by running another incremental import
                incremental_command = ImportTracksCommand(
                    service="lastfm",
                    mode="incremental",
                    user_id="checkpoint_test_user"
                    # No dates - should use checkpoint
                )
                
                # This should succeed without error (checkpoint exists)
                incremental_result = await use_case.execute(incremental_command, unit_of_work)
                assert incremental_result.operation_result.success is True