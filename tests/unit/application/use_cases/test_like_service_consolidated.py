"""Tests for like service use cases that follow Clean Architecture patterns."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.use_cases.sync_likes import (
    ExportLastFmLikesCommand,
    ExportLastFmLikesUseCase,
    ImportSpotifyLikesCommand,
    ImportSpotifyLikesUseCase,
)
from src.domain.entities.operations import OperationResult


class TestLikeUseCases:
    """Test the like use cases that follow Clean Architecture patterns."""

    @pytest.fixture
    def mock_unit_of_work(self):
        """Create mock UnitOfWork for testing."""
        uow = AsyncMock()

        # Mock repositories (non-async getters, async methods)
        mock_like_repo = AsyncMock()
        mock_track_repo = AsyncMock()
        mock_checkpoint_repo = AsyncMock()
        mock_connector_repo = AsyncMock()

        uow.get_like_repository = MagicMock(return_value=mock_like_repo)
        uow.get_track_repository = MagicMock(return_value=mock_track_repo)
        uow.get_checkpoint_repository = MagicMock(return_value=mock_checkpoint_repo)
        uow.get_connector_repository = MagicMock(return_value=mock_connector_repo)

        # Mock service connector provider (non-async)
        mock_provider = MagicMock()
        uow.get_service_connector_provider = MagicMock(return_value=mock_provider)

        return uow

    @pytest.fixture
    def mock_spotify_connector(self):
        """Create mock Spotify connector."""
        connector = AsyncMock()
        connector.get_liked_tracks = AsyncMock()
        return connector

    @pytest.fixture
    def mock_lastfm_connector(self):
        """Create mock Last.fm connector."""
        connector = AsyncMock()
        connector.love_track = AsyncMock()
        return connector

    @pytest.fixture
    def import_use_case(self):
        """Create ImportSpotifyLikesUseCase instance for testing."""
        return ImportSpotifyLikesUseCase()

    @pytest.fixture
    def export_use_case(self):
        """Create ExportLastFmLikesUseCase instance for testing."""
        return ExportLastFmLikesUseCase()

    async def test_import_spotify_use_case_follows_clean_architecture(
        self, import_use_case, mock_unit_of_work, mock_spotify_connector
    ):
        """Test that ImportSpotifyLikesUseCase follows Clean Architecture patterns."""
        # Arrange
        mock_unit_of_work.get_service_connector_provider().get_connector.return_value = mock_spotify_connector
        mock_spotify_connector.get_liked_tracks.return_value = ([], None)

        # Mock checkpoint operations
        from src.domain.entities import SyncCheckpoint

        mock_checkpoint = SyncCheckpoint(
            user_id="test", service="spotify", entity_type="likes"
        )
        mock_unit_of_work.get_checkpoint_repository().get_sync_checkpoint.return_value = mock_checkpoint
        mock_unit_of_work.get_checkpoint_repository().save_sync_checkpoint.return_value = mock_checkpoint

        command = ImportSpotifyLikesCommand(
            user_id="test_user", limit=50, max_imports=100
        )

        # Act
        result = await import_use_case.execute(command, mock_unit_of_work)

        # Assert
        assert isinstance(result, OperationResult)
        assert result.operation_name == "Spotify Likes Import"
        mock_unit_of_work.__aenter__.assert_called_once()

    async def test_export_lastfm_use_case_follows_clean_architecture(
        self, export_use_case, mock_unit_of_work, mock_lastfm_connector
    ):
        """Test that ExportLastFmLikesUseCase follows Clean Architecture patterns."""
        # Arrange
        mock_unit_of_work.get_service_connector_provider().get_connector.return_value = mock_lastfm_connector
        mock_lastfm_connector.love_track.return_value = True

        # Mock repository responses - avoid division by zero
        mock_unit_of_work.get_like_repository().get_all_liked_tracks.return_value = [
            1,
            2,
            3,
        ]  # 3 tracks
        mock_unit_of_work.get_like_repository().get_unsynced_likes.return_value = []

        # Mock checkpoint operations
        from src.domain.entities import SyncCheckpoint

        mock_checkpoint = SyncCheckpoint(
            user_id="test", service="lastfm", entity_type="likes"
        )
        mock_unit_of_work.get_checkpoint_repository().get_sync_checkpoint.return_value = mock_checkpoint
        mock_unit_of_work.get_checkpoint_repository().save_sync_checkpoint.return_value = mock_checkpoint

        command = ExportLastFmLikesCommand(
            user_id="test_user", batch_size=20, max_exports=50
        )

        # Act
        result = await export_use_case.execute(command, mock_unit_of_work)

        # Assert
        assert isinstance(result, OperationResult)
        assert result.operation_name == "Last.fm Likes Export"
        mock_unit_of_work.__aenter__.assert_called_once()


class TestOperationResultForLikeOperations:
    """Test OperationResult for like import/export operations with summary metrics."""

    def test_like_import_result_metrics(self):
        """Test that OperationResult tracks import metrics with summary metrics."""
        result = OperationResult(operation_name="Test Import")
        result.summary_metrics.add("imported", 50, "Likes Imported", significance=1)
        result.summary_metrics.add("filtered", 5, "Filtered", significance=2)
        result.summary_metrics.add("errors", 2, "Errors", significance=3)
        result.summary_metrics.add(
            "already_liked", 100, "Already Liked", significance=4
        )
        result.summary_metrics.add("candidates", 57, "Candidates", significance=5)

        # Calculate success rate
        total = 57
        success_rate = (50 / total) * 100
        result.summary_metrics.add(
            "success_rate",
            success_rate,
            "Success Rate",
            format="percent",
            significance=6,
        )

        assert result.operation_name == "Test Import"

        # Verify metrics are present
        imported = next(
            m for m in result.summary_metrics.metrics if m.name == "imported"
        )
        assert imported.value == 50

        already_liked = next(
            m for m in result.summary_metrics.metrics if m.name == "already_liked"
        )
        assert already_liked.value == 100

        success = next(
            m for m in result.summary_metrics.metrics if m.name == "success_rate"
        )
        assert success.value > 0

    def test_like_export_result_metrics(self):
        """Test that OperationResult tracks export metrics with summary metrics."""
        result = OperationResult(operation_name="Test Export")
        result.summary_metrics.add("exported", 25, "Exported", significance=1)
        result.summary_metrics.add("filtered", 3, "Filtered", significance=2)
        result.summary_metrics.add("errors", 1, "Errors", significance=3)
        result.summary_metrics.add("already_liked", 50, "Already Loved", significance=4)
        result.summary_metrics.add("candidates", 29, "Candidates", significance=5)

        # Calculate success rate
        total = 29
        success_rate = (25 / total) * 100
        result.summary_metrics.add(
            "success_rate",
            success_rate,
            "Success Rate",
            format="percent",
            significance=6,
        )

        assert result.operation_name == "Test Export"

        # Verify metrics are present
        exported = next(
            m for m in result.summary_metrics.metrics if m.name == "exported"
        )
        assert exported.value == 25

        already_liked = next(
            m for m in result.summary_metrics.metrics if m.name == "already_liked"
        )
        assert already_liked.value == 50

        success = next(
            m for m in result.summary_metrics.metrics if m.name == "success_rate"
        )
        assert success.value > 0
