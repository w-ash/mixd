"""Tests for ResolveTrackIdentityUseCase application layer.

This test suite validates the core track identity resolution orchestration logic following
Clean Architecture principles with UnitOfWork pattern and proper mocking at architectural boundaries.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.application.use_cases.resolve_track_identity import (
    ResolveTrackIdentityCommand,
    ResolveTrackIdentityResult,
    ResolveTrackIdentityUseCase,
)
from src.domain.entities.track import Artist, Track, TrackList
from src.domain.matching.types import ConfidenceEvidence, MatchResult
from src.domain.repositories import UnitOfWorkProtocol


class TestResolveTrackIdentityUseCase:
    """Test suite for ResolveTrackIdentityUseCase."""

    @pytest.fixture
    def mock_unit_of_work(self):
        """Mock UnitOfWork with track identity service."""
        mock_uow = Mock(spec=UnitOfWorkProtocol)

        # Mock track identity service
        mock_identity_service = AsyncMock()
        mock_identity_service.resolve_track_identities.return_value = {
            1: MatchResult(
                track=Track(
                    id=1,
                    title="Test Track",
                    artists=[Artist(name="Test Artist")],
                    duration_ms=180000,
                ),
                success=True,
                connector_id="spotify123",
                confidence=85,
                match_method="exact_match",
                service_data={},
                evidence=ConfidenceEvidence(
                    base_score=80,
                    title_score=0.95,
                    artist_score=0.90,
                    duration_score=0.85,
                    title_similarity=0.95,
                    artist_similarity=0.90,
                    duration_diff_ms=1000,
                    final_score=85,
                ),
            )
        }

        mock_uow.get_track_identity_service.return_value = mock_identity_service
        return mock_uow

    @pytest.fixture
    def use_case(self):
        """Create ResolveTrackIdentityUseCase instance (no dependencies in constructor)."""
        return ResolveTrackIdentityUseCase()

    @pytest.fixture
    def sample_track_with_id(self):
        """Sample track with database ID for testing."""
        return Track(
            id=1,
            title="Test Track",
            artists=[Artist(name="Test Artist")],
            duration_ms=180000,
        )

    @pytest.fixture
    def sample_track_without_id(self):
        """Sample track without database ID for testing."""
        return Track(
            id=None,
            title="No ID Track",
            artists=[Artist(name="No ID Artist")],
            duration_ms=200000,
        )

    @pytest.fixture
    def mock_connector_instance(self):
        """Mock connector instance."""
        return Mock()

    @pytest.fixture
    def basic_command(self, sample_track_with_id, mock_connector_instance):
        """Basic valid command for testing."""
        return ResolveTrackIdentityCommand(
            tracklist=TrackList(tracks=[sample_track_with_id]),
            connector="spotify",
            connector_instance=mock_connector_instance,
        )

    async def test_successful_identity_resolution(
        self, use_case, basic_command, mock_unit_of_work
    ):
        """Test successful track identity resolution."""
        # Execute the use case with UnitOfWork
        result = await use_case.execute(basic_command, mock_unit_of_work)

        # Verify result structure
        assert isinstance(result, ResolveTrackIdentityResult)
        assert result.track_count == 1
        assert result.resolved_count == 1
        assert len(result.errors) == 0
        assert result.execution_time_ms >= 0  # Timing can be 0 for very fast operations

        # Verify UnitOfWork was used to get the identity service
        mock_unit_of_work.get_track_identity_service.assert_called_once()

        # Verify service was called correctly
        mock_identity_service = (
            mock_unit_of_work.get_track_identity_service.return_value
        )
        mock_identity_service.resolve_track_identities.assert_called_once()
        call_args = mock_identity_service.resolve_track_identities.call_args

        # Check the tracklist passed to service
        passed_tracklist = call_args[0][0]
        assert len(passed_tracklist.tracks) == 1
        assert passed_tracklist.tracks[0].id == 1

        # Check other parameters
        assert call_args[0][1] == "spotify"  # connector
        assert call_args[0][2] == basic_command.connector_instance  # connector_instance

    async def test_empty_tracklist(
        self, use_case, mock_connector_instance, mock_unit_of_work
    ):
        """Test handling of empty tracklist."""
        command = ResolveTrackIdentityCommand(
            tracklist=TrackList(tracks=[]),
            connector="spotify",
            connector_instance=mock_connector_instance,
        )

        result = await use_case.execute(command, mock_unit_of_work)

        assert result.track_count == 0
        assert result.resolved_count == 0
        assert len(result.errors) == 1
        assert "No tracks with database IDs" in result.errors[0]

        # UnitOfWork service should not be called for empty tracklist
        mock_unit_of_work.get_track_identity_service.assert_not_called()

    async def test_tracks_without_database_ids(
        self,
        use_case,
        sample_track_without_id,
        mock_connector_instance,
        mock_unit_of_work,
    ):
        """Test handling of tracks without database IDs."""
        command = ResolveTrackIdentityCommand(
            tracklist=TrackList(tracks=[sample_track_without_id]),
            connector="spotify",
            connector_instance=mock_connector_instance,
        )

        result = await use_case.execute(command, mock_unit_of_work)

        assert result.track_count == 1
        assert result.resolved_count == 0
        assert len(result.errors) == 1
        assert "No tracks with database IDs" in result.errors[0]

        # UnitOfWork service should not be called when no valid tracks
        mock_unit_of_work.get_track_identity_service.assert_not_called()

    async def test_mixed_tracks_with_and_without_ids(
        self,
        use_case,
        sample_track_with_id,
        sample_track_without_id,
        mock_connector_instance,
        mock_unit_of_work,
    ):
        """Test handling of mixed tracks (some with IDs, some without)."""
        command = ResolveTrackIdentityCommand(
            tracklist=TrackList(tracks=[sample_track_with_id, sample_track_without_id]),
            connector="spotify",
            connector_instance=mock_connector_instance,
        )

        # Mock service to return result for only the valid track
        mock_identity_service = (
            mock_unit_of_work.get_track_identity_service.return_value
        )
        mock_identity_service.resolve_track_identities.return_value = {1: Mock()}

        result = await use_case.execute(command, mock_unit_of_work)

        assert result.track_count == 2  # Total tracks provided
        assert result.resolved_count == 1  # Only valid tracks resolved
        assert len(result.errors) == 0

        # Verify only valid tracks passed to service
        call_args = mock_identity_service.resolve_track_identities.call_args
        passed_tracklist = call_args[0][0]
        assert len(passed_tracklist.tracks) == 1
        assert passed_tracklist.tracks[0].id == 1

    async def test_service_exception_handling(
        self, use_case, basic_command, mock_unit_of_work
    ):
        """Test handling of exceptions from the identity service."""
        # Mock service to raise exception
        mock_identity_service = (
            mock_unit_of_work.get_track_identity_service.return_value
        )
        mock_identity_service.resolve_track_identities.side_effect = Exception(
            "Service error"
        )

        result = await use_case.execute(basic_command, mock_unit_of_work)

        assert result.track_count == 1
        assert result.resolved_count == 0
        assert len(result.errors) == 1
        assert "Track identity resolution failed: Service error" in result.errors[0]
        assert len(result.identity_mappings) == 0

    async def test_additional_options_forwarded(
        self, use_case, sample_track_with_id, mock_connector_instance, mock_unit_of_work
    ):
        """Test that additional options are forwarded to the service."""
        command = ResolveTrackIdentityCommand(
            tracklist=TrackList(tracks=[sample_track_with_id]),
            connector="spotify",
            connector_instance=mock_connector_instance,
            additional_options={"timeout": 30, "retry_count": 3},
        )

        await use_case.execute(command, mock_unit_of_work)

        # Verify additional options were passed as kwargs
        mock_identity_service = (
            mock_unit_of_work.get_track_identity_service.return_value
        )
        call_args = mock_identity_service.resolve_track_identities.call_args
        assert call_args[1]["timeout"] == 30
        assert call_args[1]["retry_count"] == 3

    async def test_no_matches_found(self, use_case, basic_command, mock_unit_of_work):
        """Test handling when no identity matches are found."""
        # Mock service to return empty results
        mock_identity_service = (
            mock_unit_of_work.get_track_identity_service.return_value
        )
        mock_identity_service.resolve_track_identities.return_value = {}

        result = await use_case.execute(basic_command, mock_unit_of_work)

        assert result.track_count == 1
        assert result.resolved_count == 0
        assert len(result.errors) == 0
        assert len(result.identity_mappings) == 0

    @patch("src.application.use_cases.resolve_track_identity.logger")
    async def test_logging_context(
        self, mock_logger, use_case, basic_command, mock_unit_of_work
    ):
        """Test that proper logging context is established."""
        await use_case.execute(basic_command, mock_unit_of_work)

        # Verify contextualize was called with expected parameters
        mock_logger.contextualize.assert_called_once_with(
            operation="resolve_track_identity_use_case",
            connector="spotify",
            track_count=1,
        )

    def test_command_validation(self, sample_track_with_id):
        """Test command validation."""
        # Test missing connector
        with pytest.raises(ValueError, match="Connector name must be specified"):
            ResolveTrackIdentityCommand(
                tracklist=TrackList(tracks=[sample_track_with_id]),
                connector="",
                connector_instance=Mock(),
            )

        # Test missing connector instance
        with pytest.raises(ValueError, match="Connector instance must be provided"):
            ResolveTrackIdentityCommand(
                tracklist=TrackList(tracks=[sample_track_with_id]),
                connector="spotify",
                connector_instance=None,
            )

    async def test_result_contains_identity_mappings(
        self, use_case, basic_command, mock_unit_of_work
    ):
        """Test that result contains the identity mappings from service."""
        # Set up expected mapping
        expected_mapping = {
            1: MatchResult(
                track=Track(
                    id=1,
                    title="Test",
                    artists=[Artist(name="Artist")],
                    duration_ms=180000,
                ),
                success=True,
                connector_id="spotify123",
                confidence=90,
                match_method="exact",
                service_data={},
                evidence=None,
            )
        }
        mock_identity_service = (
            mock_unit_of_work.get_track_identity_service.return_value
        )
        mock_identity_service.resolve_track_identities.return_value = expected_mapping

        result = await use_case.execute(basic_command, mock_unit_of_work)

        assert result.identity_mappings == expected_mapping
        assert result.resolved_count == 1

    def test_use_case_unitofwork_architecture(self):
        """Test that use case properly follows UnitOfWork architecture pattern."""
        # This test validates the Clean Architecture compliance with UnitOfWork pattern
        use_case = ResolveTrackIdentityUseCase()

        # Verify use case has no constructor dependencies (pure domain layer)
        assert not hasattr(use_case, "track_repo")
        assert not hasattr(use_case, "track_identity_service")

        # Verify use case can be instantiated without any dependencies
        assert isinstance(use_case, ResolveTrackIdentityUseCase)

        # Verify execute method requires UnitOfWork parameter
        import inspect

        sig = inspect.signature(use_case.execute)
        assert "uow" in sig.parameters
        assert sig.parameters["uow"].annotation == UnitOfWorkProtocol
