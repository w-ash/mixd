"""Unit tests for ResolveMatchReviewUseCase.

Tests accept and reject actions on match reviews, including mapping creation
on accept and error handling for invalid states.
"""

from unittest.mock import MagicMock

import pytest

from src.application.use_cases.resolve_match_review import (
    ResolveMatchReviewCommand,
    ResolveMatchReviewResult,
    ResolveMatchReviewUseCase,
)
from src.config.constants import ReviewStatus
from src.domain.entities.match_review import MatchReview
from src.domain.exceptions import NotFoundError
from tests.fixtures import make_track
from tests.fixtures.mocks import make_mock_uow


def _make_pending_review(**overrides) -> MatchReview:
    defaults = {
        "id": 1,
        "track_id": 42,
        "connector_name": "spotify",
        "connector_track_id": 100,
        "match_method": "artist_title",
        "confidence": 72,
        "match_weight": 4.5,
        "status": ReviewStatus.PENDING,
    }
    defaults.update(overrides)
    return MatchReview(**defaults)


class TestAcceptReview:
    """Accepting a review creates a track mapping."""

    async def test_accept_creates_mapping_and_updates_status(self):
        review = _make_pending_review()
        accepted_review = MatchReview(**{
            **{
                f.name: getattr(review, f.name)
                for f in review.__attrs_attrs__  # type: ignore[attr-defined]
            },
            "status": ReviewStatus.ACCEPTED,
        })

        uow = make_mock_uow()
        review_repo = uow.get_match_review_repository()
        review_repo.get_review_by_id.return_value = review
        review_repo.update_review_status.return_value = accepted_review

        connector_repo = uow.get_connector_repository()
        ct_mock = MagicMock()
        ct_mock.connector_track_identifier = "sp_track_123"
        connector_repo.get_connector_track_by_id.return_value = ct_mock
        connector_repo.map_track_to_connector.return_value = make_track(42)

        track_repo = uow.get_track_repository()
        track_repo.get_track_by_id.return_value = make_track(42)

        command = ResolveMatchReviewCommand(
            user_id="test-user", review_id=1, action="accept"
        )
        result = await ResolveMatchReviewUseCase().execute(command, uow)

        assert isinstance(result, ResolveMatchReviewResult)
        assert result.mapping_created is True
        assert result.review.status == ReviewStatus.ACCEPTED
        connector_repo.map_track_to_connector.assert_called_once()
        uow.commit.assert_called_once()


class TestRejectReview:
    """Rejecting a review marks it without creating mappings."""

    async def test_reject_updates_status_without_mapping(self):
        review = _make_pending_review()
        rejected_review = MatchReview(**{
            **{
                f.name: getattr(review, f.name)
                for f in review.__attrs_attrs__  # type: ignore[attr-defined]
            },
            "status": ReviewStatus.REJECTED,
        })

        uow = make_mock_uow()
        review_repo = uow.get_match_review_repository()
        review_repo.get_review_by_id.return_value = review
        review_repo.update_review_status.return_value = rejected_review

        command = ResolveMatchReviewCommand(
            user_id="test-user", review_id=1, action="reject"
        )
        result = await ResolveMatchReviewUseCase().execute(command, uow)

        assert result.mapping_created is False
        assert result.review.status == ReviewStatus.REJECTED
        uow.get_connector_repository().map_track_to_connector.assert_not_called()
        uow.commit.assert_called_once()


class TestResolveErrors:
    """Error cases: not found, already resolved."""

    async def test_raises_not_found_for_missing_review(self):
        uow = make_mock_uow()
        review_repo = uow.get_match_review_repository()
        review_repo.get_review_by_id.return_value = None

        command = ResolveMatchReviewCommand(
            user_id="test-user", review_id=999, action="accept"
        )
        with pytest.raises(NotFoundError, match="999"):
            await ResolveMatchReviewUseCase().execute(command, uow)

    async def test_raises_for_already_resolved_review(self):
        review = _make_pending_review(status=ReviewStatus.ACCEPTED)
        uow = make_mock_uow()
        review_repo = uow.get_match_review_repository()
        review_repo.get_review_by_id.return_value = review

        command = ResolveMatchReviewCommand(
            user_id="test-user", review_id=1, action="reject"
        )
        with pytest.raises(ValueError, match="already resolved"):
            await ResolveMatchReviewUseCase().execute(command, uow)
