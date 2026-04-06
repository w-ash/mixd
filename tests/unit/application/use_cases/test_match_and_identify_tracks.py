"""Unit tests for MatchAndIdentifyTracksUseCase.

Tests the identity resolution pipeline: existing mapping lookup, raw match
fetching, domain evaluation, mapping persistence, and review candidate persistence.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid7

import pytest

from src.application.use_cases.match_and_identify_tracks import (
    MatchAndIdentifyTracksCommand,
    MatchAndIdentifyTracksResult,
    MatchAndIdentifyTracksUseCase,
)
from src.domain.entities.match_review import MatchReview
from src.domain.entities.track import TrackList
from src.domain.matching.types import ConfidenceEvidence, EvaluationResult, MatchResult
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
            user_id="test-user",
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
                user_id="test-user",
                tracklist=tracklist,
                connector="",
                connector_instance=mock_connector,
            )

    def test_none_connector_instance_rejected(self):
        """Test that None connector instance is rejected."""
        tracklist = TrackList(tracks=[make_track()])
        with pytest.raises(ValueError, match="Connector instance must be provided"):
            MatchAndIdentifyTracksCommand(
                user_id="test-user",
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
            user_id="test-user",
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
            user_id="test-user",
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
            user_id="test-user",
            tracklist=tracklist,
            connector="spotify",
            connector_instance=mock_connector,
        )
        use_case = MatchAndIdentifyTracksUseCase()

        # The evaluation service is internal to the use case; we need to
        # patch it at class level due to slots=True

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
            user_id="test-user",
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
            user_id="test-user",
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
            user_id="test-user",
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
            user_id="test-user",
            tracklist=tracklist,
            connector="spotify",
            connector_instance=mock_connector,
            progress_manager=mock_progress,
            parent_operation_id="parent-op-1",
        )

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
            user_id="test-user",
            tracklist=tracklist,
            connector="spotify",
            connector_instance=mock_connector,
            # No progress_manager or parent_operation_id
        )

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
            user_id="test-user",
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
            user_id="test-user",
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


class TestPersistReviewCandidates:
    """Test review candidate persistence through the use case execute path."""

    @pytest.fixture
    def review_setup(self, mock_connector):
        """Set up tracks and review candidates for review persistence tests."""
        tracks = [make_track(), make_track()]
        tracklist = TrackList(tracks=tracks)

        # Build review candidates with realistic service_data
        ct_id_0 = uuid7()
        ct_id_1 = uuid7()

        review_candidates = {
            tracks[0].id: MatchResult(
                track=tracks[0],
                success=False,
                review_required=True,
                connector_id="sp_track_abc",
                confidence=65,
                match_method="artist_title",
                service_data={
                    "title": "Close Match",
                    "artists": ["Artist A"],
                    "album": "Album X",
                    "duration_ms": 240000,
                    "isrc": "USRC12345678",
                },
                evidence=ConfidenceEvidence(
                    base_score=60,
                    title_similarity=0.85,
                    artist_similarity=0.90,
                    match_weight=3.5,
                    final_score=65,
                ),
            ),
            tracks[1].id: MatchResult(
                track=tracks[1],
                success=False,
                review_required=True,
                connector_id="sp_track_def",
                confidence=55,
                match_method="artist_title",
                service_data={
                    "title": "Maybe Match",
                    "artists": ["Artist B"],
                },
            ),
        }

        # Map connector IDs to database UUIDs
        ct_id_map = {
            ("spotify", "sp_track_abc"): ct_id_0,
            ("spotify", "sp_track_def"): ct_id_1,
        }

        return {
            "tracks": tracks,
            "tracklist": tracklist,
            "review_candidates": review_candidates,
            "ct_id_map": ct_id_map,
            "ct_ids": (ct_id_0, ct_id_1),
        }

    async def test_review_candidates_persisted(
        self, mock_uow, mock_connector, review_setup
    ):
        """Review candidates create connector_tracks then MatchReview records."""
        uow = mock_uow
        identity_service = uow.get_track_identity_service()
        identity_service.get_existing_identity_mappings.return_value = {}
        identity_service.get_raw_external_matches.return_value = {}

        connector_repo = uow.get_connector_repository()
        connector_repo.ensure_connector_tracks.return_value = review_setup["ct_id_map"]

        review_repo = uow.get_match_review_repository()
        review_repo.create_reviews_batch.return_value = 2

        evaluation = EvaluationResult(
            accepted={},
            review_candidates=review_setup["review_candidates"],
        )

        command = MatchAndIdentifyTracksCommand(
            user_id="test-user",
            tracklist=review_setup["tracklist"],
            connector="spotify",
            connector_instance=mock_connector,
        )
        use_case = MatchAndIdentifyTracksUseCase()

        with patch.object(
            MatchAndIdentifyTracksUseCase,
            "_evaluation_service",
            create=True,
        ) as mock_eval:
            mock_eval.evaluate_raw_matches.return_value = evaluation
            result = await use_case.execute(command, uow)

        assert not result.errors

        # Phase 1: ensure_connector_tracks was called with correct data
        connector_repo.ensure_connector_tracks.assert_called_once()
        call_args = connector_repo.ensure_connector_tracks.call_args
        assert call_args[0][0] == "spotify"
        tracks_data = call_args[0][1]
        assert len(tracks_data) == 2
        assert tracks_data[0]["connector_id"] == "sp_track_abc"
        assert tracks_data[0]["title"] == "Close Match"
        assert tracks_data[0]["artists"] == ["Artist A"]

        # Phase 2: create_reviews_batch was called with MatchReview entities
        review_repo.create_reviews_batch.assert_called_once()
        reviews = review_repo.create_reviews_batch.call_args[0][0]
        assert len(reviews) == 2
        assert all(isinstance(r, MatchReview) for r in reviews)

        # Verify first review has correct fields
        r0 = next(
            r for r in reviews if r.connector_track_id == review_setup["ct_ids"][0]
        )
        assert r0.connector_name == "spotify"
        assert r0.match_method == "artist_title"
        assert r0.confidence == 65
        assert r0.match_weight == 3.5

    async def test_review_candidates_without_evidence_default_weight(
        self, mock_uow, mock_connector, review_setup
    ):
        """MatchReview.match_weight defaults to 0.0 when evidence is None."""
        uow = mock_uow
        identity_service = uow.get_track_identity_service()
        identity_service.get_existing_identity_mappings.return_value = {}
        identity_service.get_raw_external_matches.return_value = {}

        # Use only the second candidate (no evidence)
        track = review_setup["tracks"][1]
        no_evidence_candidate = review_setup["review_candidates"][track.id]
        candidates = {track.id: no_evidence_candidate}

        ct_id = uuid7()
        connector_repo = uow.get_connector_repository()
        connector_repo.ensure_connector_tracks.return_value = {
            ("spotify", "sp_track_def"): ct_id,
        }

        review_repo = uow.get_match_review_repository()
        review_repo.create_reviews_batch.return_value = 1

        evaluation = EvaluationResult(accepted={}, review_candidates=candidates)

        command = MatchAndIdentifyTracksCommand(
            user_id="test-user",
            tracklist=TrackList(tracks=[track]),
            connector="spotify",
            connector_instance=mock_connector,
        )
        use_case = MatchAndIdentifyTracksUseCase()

        with patch.object(
            MatchAndIdentifyTracksUseCase,
            "_evaluation_service",
            create=True,
        ) as mock_eval:
            mock_eval.evaluate_raw_matches.return_value = evaluation
            await use_case.execute(command, uow)

        reviews = review_repo.create_reviews_batch.call_args[0][0]
        assert len(reviews) == 1
        assert reviews[0].match_weight == 0.0
        assert reviews[0].confidence_evidence is None

    async def test_empty_review_candidates_no_repo_calls(
        self, mock_uow, mock_connector
    ):
        """Empty review_candidates skips connector_track and review persistence."""
        uow = mock_uow
        identity_service = uow.get_track_identity_service()
        identity_service.get_existing_identity_mappings.return_value = {}
        identity_service.get_raw_external_matches.return_value = {}

        evaluation = EvaluationResult(accepted={}, review_candidates={})

        command = MatchAndIdentifyTracksCommand(
            user_id="test-user",
            tracklist=TrackList(tracks=[make_track()]),
            connector="spotify",
            connector_instance=mock_connector,
        )
        use_case = MatchAndIdentifyTracksUseCase()

        with patch.object(
            MatchAndIdentifyTracksUseCase,
            "_evaluation_service",
            create=True,
        ) as mock_eval:
            mock_eval.evaluate_raw_matches.return_value = evaluation
            await use_case.execute(command, uow)

        # Neither repo should be called when there are no review candidates
        connector_repo = uow.get_connector_repository()
        connector_repo.ensure_connector_tracks.assert_not_called()
        review_repo = uow.get_match_review_repository()
        review_repo.create_reviews_batch.assert_not_called()
