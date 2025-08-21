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


@pytest.mark.unit
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

    def test_use_cases_have_no_constructor_dependencies(
        self, import_use_case, export_use_case
    ):
        """Test that use cases follow Clean Architecture with no constructor dependencies."""
        # Import use case should have no constructor dependencies
        assert hasattr(import_use_case, "execute")
        assert not hasattr(import_use_case, "_dependencies")

        # Export use case should have no constructor dependencies
        assert hasattr(export_use_case, "execute")
        assert not hasattr(export_use_case, "_dependencies")

    def test_use_cases_use_unit_of_work_parameter_injection(
        self, import_use_case, export_use_case
    ):
        """Test that use cases use UnitOfWork parameter injection pattern."""
        import inspect

        # Import use case should take UoW as parameter
        import_sig = inspect.signature(import_use_case.execute)
        assert "uow" in import_sig.parameters

        # Export use case should take UoW as parameter
        export_sig = inspect.signature(export_use_case.execute)
        assert "uow" in export_sig.parameters

    def test_commands_are_immutable(self):
        """Test that command objects are immutable."""
        import_cmd = ImportSpotifyLikesCommand(user_id="test", limit=50)
        export_cmd = ExportLastFmLikesCommand(user_id="test", batch_size=20)

        # Commands should be frozen (immutable)
        with pytest.raises(AttributeError):
            import_cmd.user_id = "modified"  # Should fail - frozen

        with pytest.raises(AttributeError):
            export_cmd.user_id = "modified"  # Should fail - frozen


class TestOperationResultForLikeOperations:
    """Test OperationResult for like import/export operations."""

    def test_like_import_result_metrics(self):
        """Test that OperationResult tracks import metrics correctly."""
        result = OperationResult(
            operation_name="Test Import",
            imported_count=50,
            filtered_count=5,  # Changed from skipped_count to filtered_count
            error_count=2,
            already_liked=100,
            candidates=57,
        )

        assert result.operation_name == "Test Import"
        assert result.imported_count == 50
        assert result.total_processed == 57  # 50 + 5 + 2
        assert result.success_rate > 0
        assert result.already_liked == 100

    def test_like_export_result_metrics(self):
        """Test that OperationResult tracks export metrics correctly."""
        result = OperationResult(
            operation_name="Test Export",
            exported_count=25,
            filtered_count=3,  # Changed from skipped_count to filtered_count
            error_count=1,
            already_liked=50,
            candidates=29,
        )

        assert result.operation_name == "Test Export"
        assert result.exported_count == 25
        assert result.total_processed == 29  # 25 + 3 + 1
        assert result.success_rate > 0
        assert result.already_liked == 50
