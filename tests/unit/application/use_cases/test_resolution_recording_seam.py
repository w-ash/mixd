"""Every identity decision reaches the write seam, in the caller's transaction.

The value at stake is legibility: if a mutation site can decide something and
skip the seam, the log stops being a complete answer to "why does my library
believe this" — and a partial audit trail is worse than none, because it looks
authoritative. So these tests are about *routing*, not about storage: each site
is driven with a mock UoW and checked for the event type it owes.

The negative case matters just as much. An event written for a mapping write
that did not happen would be a lie the log has no way to retract, so the seam
is only ever reached after the mutation it describes.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid7

import pytest

from src.application.use_cases.match_and_identify_tracks import (
    MatchAndIdentifyTracksCommand,
    MatchAndIdentifyTracksUseCase,
)
from src.application.use_cases.resolve_match_review import (
    ResolveMatchReviewCommand,
    ResolveMatchReviewUseCase,
)
from src.config.constants import ReviewStatus
from src.domain.entities.match_review import MatchReview
from src.domain.entities.track import Artist, ConnectorTrack, TrackList
from src.domain.matching.types import ConfidenceEvidence, EvaluationResult, MatchResult
from tests.fixtures import make_track
from tests.fixtures.mocks import make_mock_uow

_USER = "seam-user"


def _evidence(score: int) -> ConfidenceEvidence:
    return ConfidenceEvidence(base_score=score, final_score=score)


def _match(track, *, connector_id: str, confidence: int, **flags) -> MatchResult:
    return MatchResult(
        track=track,
        success=flags.get("success", False),
        review_required=flags.get("review_required", False),
        connector_id=connector_id,
        confidence=confidence,
        match_method="artist_title",
        service_data={"title": track.title, "artist": "Someone", "duration_ms": 1000},
        evidence=_evidence(confidence),
    )


@pytest.fixture
def uow():
    mock = make_mock_uow()
    identity_service = AsyncMock()
    identity_service.get_existing_identity_mappings.return_value = {}
    identity_service.get_raw_external_matches.return_value = {}
    mock.get_track_identity_service = MagicMock(return_value=identity_service)
    return mock


def _recorder(uow) -> AsyncMock:
    return uow.get_resolution_recorder.return_value


def _recorded_types(recorder: AsyncMock) -> list[str]:
    """Every event type handed to the seam via ``record``, the one write path."""
    return [
        decision.event_type
        for call in recorder.record.call_args_list
        for decision in call.args[0]
    ]


async def _run_pipeline(uow, evaluation: EvaluationResult, tracks) -> None:
    use_case = MatchAndIdentifyTracksUseCase()
    use_case._evaluation_service = MagicMock(  # pyright: ignore[reportPrivateUsage]
        evaluate_raw_matches=MagicMock(return_value=evaluation)
    )
    await use_case.execute(
        MatchAndIdentifyTracksCommand(
            user_id=_USER,
            tracklist=TrackList(tracks=tracks),
            connector="spotify",
            connector_instance=AsyncMock(),
        ),
        uow,
    )


class TestMatchingPipelineRouting:
    """Rejections and no-matches stop being discarded."""

    async def test_rejected_candidate_emits_a_rejected_event(self, uow):
        track = make_track(1)
        rejected = _match(track, connector_id="sp_bad", confidence=20)

        await _run_pipeline(uow, EvaluationResult(rejected=[rejected]), [track])

        assert "rejected" in _recorded_types(_recorder(uow))

    async def test_rejected_candidate_becomes_a_cannot_link_entry(self, uow):
        track = make_track(1)
        rejected = _match(track, connector_id="sp_bad", confidence=20)

        await _run_pipeline(uow, EvaluationResult(rejected=[rejected]), [track])

        remembered = _recorder(uow).remember_rejections.await_args
        assert remembered is not None
        (candidates,) = remembered.args
        assert [c.connector.identifier for c in candidates] == ["sp_bad"]
        assert remembered.kwargs["connector_name"] == "spotify"

    async def test_track_with_no_provider_result_emits_a_no_match_event(self, uow):
        track = make_track(2)

        await _run_pipeline(
            uow, EvaluationResult(no_match_track_ids=[track.id]), [track]
        )

        assert "no_match" in _recorded_types(_recorder(uow))

    async def test_no_match_does_not_create_a_cannot_link_entry(self, uow):
        """Nothing was refused — there is no candidate for a constraint to name."""
        track = make_track(2)

        await _run_pipeline(
            uow, EvaluationResult(no_match_track_ids=[track.id]), [track]
        )

        _recorder(uow).remember_rejections.assert_not_awaited()

    async def test_review_candidate_is_queued_with_its_selection_probability(self, uow):
        track = make_track(3)
        candidate = _match(
            track, connector_id="sp_gray", confidence=70, review_required=True
        )
        connector_repo = uow.get_connector_repository()
        ct_id = uuid7()
        connector_repo.ensure_connector_tracks.return_value = {
            ("spotify", "sp_gray"): ct_id
        }

        await _run_pipeline(
            uow, EvaluationResult(review_candidates={track.id: candidate}), [track]
        )

        queued = [
            decision
            for call in _recorder(uow).record.call_args_list
            for decision in call.args[0]
            if decision.event_type == "queued"
        ]
        assert len(queued) == 1
        assert queued[0].selection_probability == 1.0
        assert queued[0].zone == "review"
        assert queued[0].score == 70

    async def test_a_clean_pass_records_nothing(self, uow):
        """No decision, no event — the log stays proportional to what happened."""
        track = make_track(4)
        accepted = _match(track, connector_id="sp_ok", confidence=95, success=True)

        await _run_pipeline(
            uow, EvaluationResult(accepted={track.id: accepted}), [track]
        )

        assert _recorded_types(_recorder(uow)) == []


class TestReviewResolutionRouting:
    """A human's verdict is the strongest negative evidence mixd holds."""

    @staticmethod
    def _review(track_id, ct_id) -> MatchReview:
        return MatchReview(
            track_id=track_id,
            connector_name="spotify",
            connector_track_id=ct_id,
            match_method="artist_title",
            confidence=70,
            match_weight=1.0,
            status=ReviewStatus.PENDING,
        )

    async def _reject(self, uow, track):
        ct_id = uuid7()
        review = self._review(track.id, ct_id)
        review_repo = uow.get_match_review_repository()
        review_repo.get_review_by_id.return_value = review
        review_repo.update_review_status.return_value = review

        connector_repo = uow.get_connector_repository()
        connector_repo.get_connector_track_by_id.return_value = ConnectorTrack(
            id=ct_id,
            connector_name="spotify",
            connector_track_identifier="sp_gray",
            title=track.title,
            artists=[Artist(name="Someone")],
        )
        uow.get_track_repository().get_track_by_id.return_value = track

        await ResolveMatchReviewUseCase().execute(
            ResolveMatchReviewCommand(
                user_id=_USER, review_id=review.id, action="reject"
            ),
            uow,
        )

    async def test_reject_emits_a_rejected_event(self, uow):
        track = make_track(5)
        await self._reject(uow, track)
        assert "rejected" in _recorded_types(_recorder(uow))

    async def test_reject_is_sticky(self, uow):
        track = make_track(5)
        await self._reject(uow, track)
        _recorder(uow).remember_rejections.assert_awaited_once()

    async def test_reject_events_ride_the_use_case_commit(self, uow):
        """The event is written before the commit that makes the verdict durable."""
        track = make_track(5)
        await self._reject(uow, track)
        uow.commit.assert_awaited_once()
        _recorder(uow).record.assert_awaited_once()


class TestFailedWritesLeaveNoTrace:
    """An event for a mutation that did not happen is a lie with no retraction."""

    async def test_review_accept_that_cannot_find_its_connector_track_records_nothing(
        self, uow
    ):
        review = MatchReview(
            track_id=uuid7(),
            connector_name="spotify",
            connector_track_id=uuid7(),
            match_method="artist_title",
            confidence=70,
            match_weight=1.0,
            status=ReviewStatus.PENDING,
        )
        review_repo = uow.get_match_review_repository()
        review_repo.get_review_by_id.return_value = review
        uow.get_connector_repository().get_connector_track_by_id.return_value = None

        with pytest.raises(Exception, match="no longer exists"):
            await ResolveMatchReviewUseCase().execute(
                ResolveMatchReviewCommand(
                    user_id=_USER, review_id=review.id, action="accept"
                ),
                uow,
            )

        _recorder(uow).record.assert_not_awaited()
        uow.commit.assert_not_awaited()
