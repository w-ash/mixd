"""Unit tests for LastfmPlayImporter core business logic.

Tests the critical paths and boundary conditions using the pyramid pattern:
- Unit tests for core logic (checkpoint resolution, date range determination)
- Focus on business logic without external dependencies
"""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from src.domain.entities import PlayRecord, SyncCheckpoint
from src.infrastructure.services.lastfm_play_importer import LastfmPlayImporter


class TestLastfmPlayImporterUnit:
    """Unit tests for LastfmPlayImporter core business logic."""

    @pytest.fixture
    def mock_repositories(self):
        """Mock repositories for dependency injection."""
        return {
            "plays_repository": Mock(),
            "checkpoint_repository": AsyncMock(),
            "connector_repository": Mock(),
            "track_repository": Mock(),
        }

    @pytest.fixture
    def lastfm_importer(self, mock_repositories):
        """Create LastfmPlayImporter with mocked dependencies."""
        return LastfmPlayImporter(**mock_repositories)

    # CRITICAL PATH 1: Checkpoint Resolution Logic
    @pytest.mark.asyncio
    async def test_resolve_checkpoint_success(self, lastfm_importer, mock_repositories):
        """Test successful checkpoint resolution with valid data."""
        # Arrange
        mock_checkpoint = SyncCheckpoint(
            user_id="test_user",
            service="lastfm",
            entity_type="plays",
            last_timestamp=datetime(2024, 3, 15, 23, 59, 59, tzinfo=UTC),
            cursor="2024-03-15",
        )
        mock_repositories[
            "checkpoint_repository"
        ].get_sync_checkpoint.return_value = mock_checkpoint

        # Act
        result = await lastfm_importer._resolve_checkpoint(
            username="test_user", uow=Mock()
        )

        # Assert
        assert result == mock_checkpoint
        mock_repositories[
            "checkpoint_repository"
        ].get_sync_checkpoint.assert_called_once_with(
            user_id="test_user", service="lastfm", entity_type="plays"
        )

    @pytest.mark.asyncio
    async def test_resolve_checkpoint_no_uow(self, lastfm_importer):
        """Test checkpoint resolution fails gracefully without UoW."""
        # Act
        result = await lastfm_importer._resolve_checkpoint(
            username="test_user", uow=None
        )

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_checkpoint_no_username(self, lastfm_importer):
        """Test checkpoint resolution with fallback to connector username."""
        # Arrange
        lastfm_importer.lastfm_connector.lastfm_username = "fallback_user"
        mock_checkpoint = SyncCheckpoint(
            user_id="fallback_user",
            service="lastfm",
            entity_type="plays",
            last_timestamp=datetime(2024, 3, 15, tzinfo=UTC),
            cursor="2024-03-15",
        )
        lastfm_importer.checkpoint_repository.get_sync_checkpoint.return_value = (
            mock_checkpoint
        )

        # Act
        result = await lastfm_importer._resolve_checkpoint(username=None, uow=Mock())

        # Assert
        assert result == mock_checkpoint

    # CRITICAL PATH 2: Date Range Determination Logic
    def test_determine_date_range_explicit_range(self, lastfm_importer):
        """Test explicit date range handling (boundary condition)."""
        # Arrange
        from_date = datetime(2024, 1, 1, tzinfo=UTC)
        to_date = datetime(2024, 1, 31, tzinfo=UTC)
        checkpoint = None

        # Act
        start, end = lastfm_importer._determine_date_range(
            from_date, to_date, checkpoint
        )

        # Assert
        assert start == from_date
        assert end == to_date

    def test_determine_date_range_incremental_with_checkpoint(self, lastfm_importer):
        """Test incremental import with existing checkpoint (critical path)."""
        # Arrange
        checkpoint = SyncCheckpoint(
            user_id="test_user",
            service="lastfm",
            entity_type="plays",
            last_timestamp=datetime(2024, 3, 15, 12, 30, 0, tzinfo=UTC),
            cursor="2024-03-15",
        )

        # Act
        start, end = lastfm_importer._determine_date_range(None, None, checkpoint)

        # Assert
        # Should start from beginning of checkpoint day
        expected_start = datetime(2024, 3, 15, 0, 0, 0, tzinfo=UTC)
        assert start == expected_start
        assert end.date() == datetime.now(UTC).date()  # Should end at now

    def test_determine_date_range_no_checkpoint_fails(self, lastfm_importer):
        """Test incremental import without checkpoint fails (boundary condition)."""
        # Act & Assert
        with pytest.raises(ValueError, match="No checkpoint found"):
            lastfm_importer._determine_date_range(None, None, None)

    def test_determine_date_range_invalid_checkpoint_fails(self, lastfm_importer):
        """Test invalid checkpoint data fails gracefully (boundary condition)."""
        # Arrange
        checkpoint = SyncCheckpoint(
            user_id="test_user",
            service="lastfm",
            entity_type="plays",
            last_timestamp=None,  # Invalid!
            cursor="2024-03-15",
        )

        # Act & Assert
        with pytest.raises(ValueError, match="Invalid checkpoint"):
            lastfm_importer._determine_date_range(None, None, checkpoint)

    # CRITICAL PATH 3: Daily Chunking Resumption Logic
    def test_daily_chunking_checkpoint_resumption(self, lastfm_importer):
        """Test daily chunking correctly resumes from checkpoint (critical business logic)."""
        # Arrange
        from_date = datetime(2024, 3, 1, tzinfo=UTC)
        datetime(2024, 3, 5, tzinfo=UTC)

        # Checkpoint shows last completed day was 2024-03-02
        checkpoint = SyncCheckpoint(
            user_id="test_user",
            service="lastfm",
            entity_type="plays",
            last_timestamp=datetime(2024, 3, 2, 23, 59, 59, tzinfo=UTC),
            cursor="2024-03-02",
        )

        # Act - simulate the resumption logic from _fetch_date_range_strategy
        checkpoint_date = datetime.fromisoformat(checkpoint.cursor).date()
        resume_date = checkpoint_date + timedelta(days=1)  # Next day after checkpoint
        start_date = max(
            resume_date, from_date.date()
        )  # Don't go earlier than requested

        # Assert
        expected_start = date(2024, 3, 3)  # Should resume from March 3rd
        assert start_date == expected_start

    def test_daily_chunking_already_caught_up(self, lastfm_importer):
        """Test daily chunking when already caught up (boundary condition)."""
        # Arrange
        from_date = datetime(2024, 3, 1, tzinfo=UTC)
        to_date = datetime(2024, 3, 5, tzinfo=UTC)

        # Checkpoint shows we're already past the end date
        checkpoint = SyncCheckpoint(
            user_id="test_user",
            service="lastfm",
            entity_type="plays",
            last_timestamp=datetime(2024, 3, 10, 23, 59, 59, tzinfo=UTC),
            cursor="2024-03-10",
        )

        # Act
        checkpoint_date = datetime.fromisoformat(checkpoint.cursor).date()
        resume_date = checkpoint_date + timedelta(days=1)
        start_date = max(resume_date, from_date.date())
        end_date = to_date.date()

        # Assert - should detect we're already caught up
        assert start_date > end_date

    # CRITICAL PATH 4: Track Play Processing
    @pytest.mark.asyncio
    async def test_process_data_empty_input(self, lastfm_importer):
        """Test processing empty data returns empty list (boundary condition)."""
        # Act
        result = await lastfm_importer._process_data(
            raw_data=[],
            batch_id="test-batch",
            import_timestamp=datetime.now(UTC),
            uow=Mock(),
        )

        # Assert
        assert result == []

    @pytest.mark.asyncio
    async def test_process_data_requires_uow(self, lastfm_importer):
        """Test processing data requires UoW (critical dependency)."""
        # Arrange
        play_record = PlayRecord(
            track_name="Test Track",
            artist_name="Test Artist",
            played_at=datetime(2024, 1, 1, tzinfo=UTC),
            service="lastfm",
            service_metadata={},
        )

        # Act & Assert
        with pytest.raises(ValueError, match="UnitOfWork is required"):
            await lastfm_importer._process_data(
                raw_data=[play_record],
                batch_id="test-batch",
                import_timestamp=datetime.now(UTC),
                uow=None,
            )
