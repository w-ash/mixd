"""End-to-end tests for LastFM import workflow.

Tests the complete import pipeline from use case to database:
- Application layer use case orchestration
- Service layer business logic
- Repository layer persistence
- Critical error paths and recovery
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.application.use_cases.import_play_history import (
    ImportTracksCommand,
    ImportTracksUseCase,
)
from src.domain.entities import PlayRecord


@pytest.mark.slow
@pytest.mark.integration
class TestLastfmImportE2E:
    """End-to-end tests for complete LastFM import workflow."""

    @pytest.fixture
    def unit_of_work(self, db_session):
        """Real UnitOfWork for E2E testing using isolated test database."""
        from src.infrastructure.persistence.repositories.factories import (
            get_unit_of_work,
        )

        return get_unit_of_work(db_session)

    # E2E TEST 1: Complete Incremental Import Success Path
    @pytest.mark.asyncio
    async def test_complete_incremental_import_success(
        self, unit_of_work, test_data_tracker
    ):
        """Test complete incremental import from use case to database."""

        # Create test track in database before starting the test
        from src.domain.entities import Artist, Track

        track = Track(
            title="Test Song",
            artists=[Artist(name="Test Artist")],
            album="Test Album",
        )
        track_repo = unit_of_work.get_track_repository()
        test_track = await track_repo.save_track(track)
        await unit_of_work.commit()

        with patch(
            "src.infrastructure.connectors.lastfm.connector.LastFMConnector"
        ) as mock_connector_class:
            # Mock connector setup
            mock_connector = Mock()
            mock_connector.lastfm_username = "e2e_test_user"
            mock_connector_class.return_value = mock_connector

            # Make the connector method async - return different records for different time periods
            async def mock_get_recent_tracks(*args, **kwargs):
                from_time = kwargs.get("from_time")
                if from_time and from_time.day == 1:  # First day
                    return [
                        PlayRecord(
                            track_name="Test Song",
                            artist_name="Test Artist",
                            album_name="Test Album",
                            played_at=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
                            service="lastfm",
                            service_metadata={"test": "metadata"},
                        )
                    ]
                else:  # Other days - return empty to avoid confusion
                    return []

            mock_connector.get_recent_tracks = mock_get_recent_tracks

            with patch(
                "src.infrastructure.connectors.lastfm.track_resolution_service.LastfmTrackResolutionService"
            ) as mock_resolution_class:
                # Mock track resolution - return successfully resolved Track entity
                mock_resolution_service = AsyncMock()
                from src.domain.entities import Artist, Track

                # Use the pre-created track from the test setup
                async def mock_resolve_plays(*args, **kwargs):
                    return [test_track], {
                        "new_tracks_count": 1,
                        "updated_tracks_count": 0,
                    }

                mock_resolution_service.resolve_plays_to_canonical_tracks.side_effect = mock_resolve_plays
                mock_resolution_class.return_value = mock_resolution_service

                # Arrange - Create command for incremental import
                command = ImportTracksCommand(
                    service="lastfm",
                    mode="incremental",
                    user_id="e2e_test_user",
                    from_date=datetime(2024, 1, 1, tzinfo=UTC),
                    to_date=datetime(2024, 1, 2, tzinfo=UTC),
                )

                # Act - Execute complete import
                use_case = ImportTracksUseCase()
                result = await use_case.execute(command, unit_of_work)

                # Assert - Verify successful import
                # Extract error count from summary metrics
                error_metric = next(
                    (
                        m.value
                        for m in result.operation_result.summary_metrics.metrics
                        if m.name == "errors"
                    ),
                    0,
                )
                assert error_metric == 0

                # Extract track plays count from summary metrics
                track_plays_metric = next(
                    (
                        m.value
                        for m in result.operation_result.summary_metrics.metrics
                        if m.name == "track_plays"
                    ),
                    None,
                )
                assert track_plays_metric is not None
                assert track_plays_metric >= 0
                assert result.service == "lastfm"
                assert result.mode == "incremental"
                assert result.execution_time_ms > 0

    # E2E TEST 2: Error Recovery - API Failure
    @pytest.mark.asyncio
    async def test_api_failure_error_handling(self, unit_of_work, test_data_tracker):
        """Test E2E error handling when LastFM API fails."""

        with patch(
            "src.infrastructure.connectors.lastfm.connector.LastFMConnector"
        ) as mock_connector_class:
            # Mock connector that fails
            mock_connector = Mock()
            mock_connector.lastfm_username = "error_test_user"

            # Make the connector method async that raises exception
            async def mock_get_recent_tracks_error(*args, **kwargs):
                raise Exception("API Error")

            mock_connector.get_recent_tracks = mock_get_recent_tracks_error
            mock_connector_class.return_value = mock_connector

            # Arrange
            command = ImportTracksCommand(
                service="lastfm",
                mode="incremental",
                user_id="error_test_user",
                from_date=datetime(2024, 1, 1, tzinfo=UTC),
                to_date=datetime(2024, 1, 2, tzinfo=UTC),
            )

            # Act
            use_case = ImportTracksUseCase()
            result = await use_case.execute(command, unit_of_work)

            # Assert - Should return error result, not raise exception
            # Extract error count from summary metrics
            error_metric = next(
                (
                    m.value
                    for m in result.operation_result.summary_metrics.metrics
                    if m.name == "errors"
                ),
                0,
            )
            assert error_metric > 0

            # Extract track plays count from summary metrics
            track_plays_metric = next(
                (
                    m.value
                    for m in result.operation_result.summary_metrics.metrics
                    if m.name == "track_plays"
                ),
                0,
            )
            assert track_plays_metric == 0

            # Note: failed_batches might be 0 since error occurs during setup, not batch processing
            # Error details are in metadata
            assert "errors" in str(result.operation_result.metadata)

    # E2E TEST 3: Boundary Condition - Empty Data
    @pytest.mark.asyncio
    async def test_empty_data_handling(self, unit_of_work, test_data_tracker):
        """Test E2E handling when no data is available to import."""

        with patch(
            "src.infrastructure.connectors.lastfm.connector.LastFMConnector"
        ) as mock_connector_class:
            # Mock connector returning empty data
            mock_connector = Mock()
            mock_connector.lastfm_username = "empty_test_user"

            # Make the connector method async
            async def mock_get_recent_tracks_empty(*args, **kwargs):
                return []

            mock_connector.get_recent_tracks = mock_get_recent_tracks_empty
            mock_connector_class.return_value = mock_connector

            # Arrange
            command = ImportTracksCommand(
                service="lastfm",
                mode="incremental",
                user_id="empty_test_user",
                from_date=datetime(2024, 1, 1, tzinfo=UTC),
                to_date=datetime(2024, 1, 2, tzinfo=UTC),
            )

            # Act
            use_case = ImportTracksUseCase()
            result = await use_case.execute(command, unit_of_work)

            # Assert - Should handle empty data gracefully
            # Extract error count from summary metrics
            error_metric = next(
                (
                    m.value
                    for m in result.operation_result.summary_metrics.metrics
                    if m.name == "errors"
                ),
                0,
            )
            assert error_metric == 0

            # Extract track plays count from summary metrics
            track_plays_metric = next(
                (
                    m.value
                    for m in result.operation_result.summary_metrics.metrics
                    if m.name == "track_plays"
                ),
                0,
            )
            assert track_plays_metric == 0

            # Extract raw plays count from summary metrics
            raw_plays_metric = next(
                (
                    m.value
                    for m in result.operation_result.summary_metrics.metrics
                    if m.name == "raw_plays"
                ),
                0,
            )
            assert raw_plays_metric == 0

    # E2E TEST 4: Critical Path - Checkpoint Persistence
    @pytest.mark.asyncio
    async def test_checkpoint_persistence_e2e(self, unit_of_work, test_data_tracker):
        """Test checkpoint creation and persistence through complete workflow."""

        # Create test track in database before starting the test
        from src.domain.entities import Artist, Track

        track = Track(
            title="Checkpoint Test",
            artists=[Artist(name="Test Artist")],
        )
        track_repo = unit_of_work.get_track_repository()
        test_track = await track_repo.save_track(track)
        await unit_of_work.commit()

        with patch(
            "src.infrastructure.connectors.lastfm.connector.LastFMConnector"
        ) as mock_connector_class:
            mock_connector = Mock()
            mock_connector.lastfm_username = "checkpoint_test_user"

            # Make the connector method async - track calls to differentiate between imports
            call_count = 0

            async def mock_get_recent_tracks_checkpoint(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                # First call (explicit dates) returns data, second call (checkpoint) returns empty
                if call_count == 1:
                    return [
                        PlayRecord(
                            track_name="Checkpoint Test",
                            artist_name="Test Artist",
                            played_at=datetime(2024, 2, 15, 15, 30, tzinfo=UTC),
                            service="lastfm",
                            service_metadata={},
                        )
                    ]
                else:
                    # Second call should find no new data (checkpoint is recent)
                    return []

            mock_connector.get_recent_tracks = mock_get_recent_tracks_checkpoint
            mock_connector_class.return_value = mock_connector

            with patch(
                "src.infrastructure.connectors.lastfm.track_resolution_service.LastfmTrackResolutionService"
            ) as mock_resolution_class:
                mock_resolution_service = AsyncMock()
                from src.domain.entities import Artist, Track

                # Use the pre-created track from the test setup
                async def mock_resolve_plays(play_records, *args, **kwargs):
                    if not play_records:
                        # No plays to resolve
                        return [], {"new_tracks_count": 0, "updated_tracks_count": 0}

                    # Return the pre-created test track
                    return [test_track], {
                        "new_tracks_count": 1,
                        "updated_tracks_count": 0,
                    }

                mock_resolution_service.resolve_plays_to_canonical_tracks.side_effect = mock_resolve_plays
                mock_resolution_class.return_value = mock_resolution_service

                # Act - Run import with specific date range
                command = ImportTracksCommand(
                    service="lastfm",
                    mode="incremental",
                    user_id="checkpoint_test_user",
                    from_date=datetime(2024, 2, 15, tzinfo=UTC),
                    to_date=datetime(2024, 2, 16, tzinfo=UTC),
                )

                use_case = ImportTracksUseCase()
                result = await use_case.execute(command, unit_of_work)

                # Assert - Import succeeded
                # Extract error count from summary metrics
                error_metric = next(
                    (
                        m.value
                        for m in result.operation_result.summary_metrics.metrics
                        if m.name == "errors"
                    ),
                    0,
                )
                assert error_metric == 0

                # Verify checkpoint was created by running another incremental import
                incremental_command = ImportTracksCommand(
                    service="lastfm",
                    mode="incremental",
                    user_id="checkpoint_test_user",
                    # No dates - should use checkpoint
                )

                # This should succeed without error (checkpoint exists)
                incremental_result = await use_case.execute(
                    incremental_command, unit_of_work
                )
                # Extract error count from incremental result
                incremental_error_metric = next(
                    (
                        m.value
                        for m in incremental_result.operation_result.summary_metrics.metrics
                        if m.name == "errors"
                    ),
                    0,
                )
                assert incremental_error_metric == 0
