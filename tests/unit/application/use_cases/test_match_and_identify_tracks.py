"""Unit tests for MatchAndIdentifyTracksUseCase.

Tests the identity resolution pipeline: existing mapping lookup, raw match
fetching, domain evaluation, and mapping persistence.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.use_cases.match_and_identify_tracks import (
    MatchAndIdentifyTracksCommand,
    MatchAndIdentifyTracksResult,
    MatchAndIdentifyTracksUseCase,
)
from src.domain.entities.track import TrackList
from tests.fixtures import make_track
from tests.fixtures.mocks import make_mock_uow


@pytest.fixture
def mock_connector():
    """Mock connector instance for identity resolution."""
    return AsyncMock()


@pytest.fixture
def mock_uow():
    """Mock UnitOfWork with track identity service."""
    uow = make_mock_uow()

    identity_service = AsyncMock()
    identity_service.get_existing_identity_mappings.return_value = {}
    identity_service.get_raw_external_matches.return_value = {}
    identity_service.persist_identity_mappings.return_value = None
    uow.get_track_identity_service = MagicMock(return_value=identity_service)

    return uow


class TestMatchAndIdentifyTracksCommand:
    """Test command construction and validation."""

    def test_valid_command(self, mock_connector):
        """Test creating a valid command."""
        tracklist = TrackList(tracks=[make_track()])
        cmd = MatchAndIdentifyTracksCommand(
            tracklist=tracklist,
            connector="spotify",
            connector_instance=mock_connector,
        )
        assert cmd.connector == "spotify"
        assert len(cmd.tracklist.tracks) == 1

    def test_empty_connector_name_rejected(self, mock_connector):
        """Test that empty connector name is rejected."""
        tracklist = TrackList(tracks=[make_track()])
        with pytest.raises(ValueError, match="Connector name must be specified"):
            MatchAndIdentifyTracksCommand(
                tracklist=tracklist,
                connector="",
                connector_instance=mock_connector,
            )

    def test_none_connector_instance_rejected(self):
        """Test that None connector instance is rejected."""
        tracklist = TrackList(tracks=[make_track()])
        with pytest.raises(ValueError, match="Connector instance must be provided"):
            MatchAndIdentifyTracksCommand(
                tracklist=tracklist,
                connector="spotify",
                connector_instance=None,
            )


class TestMatchAndIdentifyTracksUseCase:
    """Test use case execution paths."""

    async def test_empty_tracklist_returns_early(self, mock_uow, mock_connector):
        """Test that empty tracklist returns immediately with zero counts."""
        tracklist = TrackList(tracks=[])
        command = MatchAndIdentifyTracksCommand(
            tracklist=tracklist,
            connector="spotify",
            connector_instance=mock_connector,
        )
        use_case = MatchAndIdentifyTracksUseCase()

        result = await use_case.execute(command, mock_uow)

        assert isinstance(result, MatchAndIdentifyTracksResult)
        assert result.track_count == 0
        assert result.resolved_count == 0
        assert result.identity_mappings == {}
        assert not result.errors

    async def test_all_tracks_already_have_mappings(self, mock_uow, mock_connector):
        """Test that existing mappings are returned without re-resolution."""
        tracks = [make_track(), make_track()]
        tracklist = TrackList(tracks=tracks)

        # All tracks already have mappings
        identity_service = mock_uow.get_track_identity_service()
        existing = {
            tracks[0].id: MagicMock(),  # Already resolved
            tracks[1].id: MagicMock(),
        }
        identity_service.get_existing_identity_mappings.return_value = existing

        command = MatchAndIdentifyTracksCommand(
            tracklist=tracklist,
            connector="spotify",
            connector_instance=mock_connector,
        )
        use_case = MatchAndIdentifyTracksUseCase()

        result = await use_case.execute(command, mock_uow)

        assert result.resolved_count == 2
        assert not result.errors
        # Should NOT have called get_raw_external_matches (no tracks need resolution)
        identity_service.get_raw_external_matches.assert_not_called()

    async def test_new_tracks_resolved_and_persisted(self, mock_uow, mock_connector):
        """Test full resolution pipeline for unresolved tracks."""
        tracks = [make_track(), make_track()]
        tracklist = TrackList(tracks=tracks)

        identity_service = mock_uow.get_track_identity_service()
        # Track 0 already mapped, track 1 needs resolution
        identity_service.get_existing_identity_mappings.return_value = {
            tracks[0].id: MagicMock(),
        }

        # Raw matches from infrastructure
        raw_matches = {tracks[1].id: {"connector_id": "spotify_2", "confidence": 90}}
        identity_service.get_raw_external_matches.return_value = raw_matches

        command = MatchAndIdentifyTracksCommand(
            tracklist=tracklist,
            connector="spotify",
            connector_instance=mock_connector,
        )
        use_case = MatchAndIdentifyTracksUseCase()

        # The evaluation service is internal to the use case; we need to
        # patch it at class level due to slots=True

        from src.domain.matching.types import EvaluationResult

        evaluation = EvaluationResult(
            accepted={tracks[1].id: MagicMock()},
            review_candidates={},
        )
        with patch.object(
            MatchAndIdentifyTracksUseCase,
            "_evaluation_service",
            create=True,
        ) as mock_eval:
            mock_eval.evaluate_raw_matches.return_value = evaluation
            result = await use_case.execute(command, mock_uow)

        assert result.resolved_count == 2  # 1 existing + 1 new
        assert not result.errors

    async def test_resolution_error_captured_in_result(self, mock_uow, mock_connector):
        """Test that exceptions during resolution are captured, not propagated."""
        tracks = [make_track()]
        tracklist = TrackList(tracks=tracks)

        identity_service = mock_uow.get_track_identity_service()
        identity_service.get_existing_identity_mappings.return_value = {}
        identity_service.get_raw_external_matches.side_effect = RuntimeError(
            "API timeout"
        )

        command = MatchAndIdentifyTracksCommand(
            tracklist=tracklist,
            connector="spotify",
            connector_instance=mock_connector,
        )
        use_case = MatchAndIdentifyTracksUseCase()

        result = await use_case.execute(command, mock_uow)

        assert result.resolved_count == 0
        assert len(result.errors) == 1
        assert "API timeout" in result.errors[0]

    async def test_result_includes_execution_time(self, mock_uow, mock_connector):
        """Test that result includes non-negative execution time."""
        tracklist = TrackList(tracks=[])
        command = MatchAndIdentifyTracksCommand(
            tracklist=tracklist,
            connector="spotify",
            connector_instance=mock_connector,
        )
        use_case = MatchAndIdentifyTracksUseCase()

        result = await use_case.execute(command, mock_uow)

        assert result.execution_time_ms >= 0

    async def test_all_tracks_have_ids_and_resolve(self, mock_uow, mock_connector):
        """Test that all tracks with UUIDs are processed and resolved."""
        tracks = [make_track(), make_track(), make_track()]
        tracklist = TrackList(tracks=tracks)

        identity_service = mock_uow.get_track_identity_service()
        identity_service.get_existing_identity_mappings.return_value = {
            tracks[0].id: MagicMock(),
            tracks[1].id: MagicMock(),
            tracks[2].id: MagicMock(),
        }

        command = MatchAndIdentifyTracksCommand(
            tracklist=tracklist,
            connector="spotify",
            connector_instance=mock_connector,
        )
        use_case = MatchAndIdentifyTracksUseCase()

        result = await use_case.execute(command, mock_uow)

        assert result.track_count == 3  # All tracks counted
        assert result.resolved_count == 3  # All tracks resolved (all have UUIDs)


class TestMatchAndIdentifyTracksProgress:
    """Test progress reporting integration in the use case."""

    async def test_creates_sub_operation_when_progress_manager_provided(
        self, mock_uow, mock_connector
    ):
        """Use case creates a matching sub-operation and completes it on success."""
        tracks = [make_track(), make_track()]
        tracklist = TrackList(tracks=tracks)

        identity_service = mock_uow.get_track_identity_service()
        identity_service.get_existing_identity_mappings.return_value = {}
        identity_service.get_raw_external_matches.return_value = {}

        mock_progress = AsyncMock()
        mock_progress.start_operation = AsyncMock(return_value="sub-op-1")
        mock_progress.emit_progress = AsyncMock()
        mock_progress.complete_operation = AsyncMock()

        command = MatchAndIdentifyTracksCommand(
            tracklist=tracklist,
            connector="spotify",
            connector_instance=mock_connector,
            progress_manager=mock_progress,
            parent_operation_id="parent-op-1",
        )

        from src.domain.matching.types import EvaluationResult

        evaluation = EvaluationResult(accepted={}, review_candidates={})

        use_case = MatchAndIdentifyTracksUseCase()
        with patch.object(
            MatchAndIdentifyTracksUseCase,
            "_evaluation_service",
            create=True,
        ) as mock_eval:
            mock_eval.evaluate_raw_matches.return_value = evaluation
            result = await use_case.execute(command, mock_uow)

        assert not result.errors

        # Verify sub-operation was started
        mock_progress.start_operation.assert_called_once()
        start_op_args = mock_progress.start_operation.call_args.args[0]
        assert start_op_args.description == "Matching tracks to spotify"
        assert start_op_args.total_items == 2

        # Verify sub-operation was completed
        mock_progress.complete_operation.assert_called_once()

        # Verify progress_callback was passed to get_raw_external_matches
        call_kwargs = identity_service.get_raw_external_matches.call_args.kwargs
        assert call_kwargs["progress_callback"] is not None

    async def test_no_sub_operation_without_progress_manager(
        self, mock_uow, mock_connector
    ):
        """Use case does not create sub-operations when progress_manager is None."""
        tracks = [make_track()]
        tracklist = TrackList(tracks=tracks)

        identity_service = mock_uow.get_track_identity_service()
        identity_service.get_existing_identity_mappings.return_value = {}
        identity_service.get_raw_external_matches.return_value = {}

        command = MatchAndIdentifyTracksCommand(
            tracklist=tracklist,
            connector="spotify",
            connector_instance=mock_connector,
            # No progress_manager or parent_operation_id
        )

        from src.domain.matching.types import EvaluationResult

        evaluation = EvaluationResult(accepted={}, review_candidates={})

        use_case = MatchAndIdentifyTracksUseCase()
        with patch.object(
            MatchAndIdentifyTracksUseCase,
            "_evaluation_service",
            create=True,
        ) as mock_eval:
            mock_eval.evaluate_raw_matches.return_value = evaluation
            result = await use_case.execute(command, mock_uow)

        assert not result.errors

        # Verify progress_callback was None (no progress_manager provided)
        call_kwargs = identity_service.get_raw_external_matches.call_args.kwargs
        assert call_kwargs["progress_callback"] is None

    async def test_sub_operation_completed_as_failed_on_error(
        self, mock_uow, mock_connector
    ):
        """Sub-operation is marked as failed when matching raises an exception."""
        tracks = [make_track()]
        tracklist = TrackList(tracks=tracks)

        identity_service = mock_uow.get_track_identity_service()
        identity_service.get_existing_identity_mappings.return_value = {}
        identity_service.get_raw_external_matches.side_effect = RuntimeError(
            "API error"
        )

        mock_progress = AsyncMock()
        mock_progress.start_operation = AsyncMock(return_value="sub-op-fail")
        mock_progress.emit_progress = AsyncMock()
        mock_progress.complete_operation = AsyncMock()

        command = MatchAndIdentifyTracksCommand(
            tracklist=tracklist,
            connector="lastfm",
            connector_instance=mock_connector,
            progress_manager=mock_progress,
            parent_operation_id="parent-op-2",
        )

        use_case = MatchAndIdentifyTracksUseCase()
        result = await use_case.execute(command, mock_uow)

        # Error should be captured in result
        assert len(result.errors) == 1
        assert "API error" in result.errors[0]

        # Sub-operation was started
        mock_progress.start_operation.assert_called_once()

        # Sub-operation was completed as FAILED
        from src.domain.entities.progress import OperationStatus

        mock_progress.complete_operation.assert_called_once()
        complete_args = mock_progress.complete_operation.call_args.args
        assert complete_args[0] == "sub-op-fail"
        assert complete_args[1] == OperationStatus.FAILED

    async def test_no_sub_operation_when_all_tracks_already_mapped(
        self, mock_uow, mock_connector
    ):
        """No sub-operation is created when all tracks already have mappings."""
        tracks = [make_track()]
        tracklist = TrackList(tracks=tracks)

        identity_service = mock_uow.get_track_identity_service()
        identity_service.get_existing_identity_mappings.return_value = {
            tracks[0].id: MagicMock(),
        }

        mock_progress = AsyncMock()
        mock_progress.start_operation = AsyncMock(return_value="sub-op-unused")

        command = MatchAndIdentifyTracksCommand(
            tracklist=tracklist,
            connector="spotify",
            connector_instance=mock_connector,
            progress_manager=mock_progress,
            parent_operation_id="parent-op-3",
        )

        use_case = MatchAndIdentifyTracksUseCase()
        result = await use_case.execute(command, mock_uow)

        assert result.resolved_count == 1
        assert not result.errors

        # No sub-operation created because all tracks were already mapped
        mock_progress.start_operation.assert_not_called()
        identity_service.get_raw_external_matches.assert_not_called()
