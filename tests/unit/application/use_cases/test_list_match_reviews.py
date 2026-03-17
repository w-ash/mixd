"""Unit tests for ListMatchReviewsUseCase.

Tests paginated retrieval of pending match reviews from the review queue.
"""

from src.application.use_cases.list_match_reviews import (
    ListMatchReviewsCommand,
    ListMatchReviewsResult,
    ListMatchReviewsUseCase,
)
from src.domain.entities.match_review import MatchReview
from tests.fixtures.mocks import make_mock_uow


def _make_review(**overrides) -> MatchReview:
    defaults = {
        "track_id": 1,
        "connector_name": "spotify",
        "connector_track_id": 100,
        "match_method": "artist_title",
        "confidence": 72,
        "match_weight": 4.5,
    }
    defaults.update(overrides)
    return MatchReview(**defaults)


class TestListMatchReviewsHappyPath:
    """Successful listing returns paginated reviews."""

    async def test_returns_reviews_with_pagination(self):
        reviews = [_make_review(id=1), _make_review(id=2, track_id=2)]
        uow = make_mock_uow()
        review_repo = uow.get_match_review_repository()
        review_repo.list_pending_reviews.return_value = (reviews, 2)

        command = ListMatchReviewsCommand(limit=50, offset=0)
        result = await ListMatchReviewsUseCase().execute(command, uow)

        assert isinstance(result, ListMatchReviewsResult)
        assert len(result.reviews) == 2
        assert result.total == 2
        assert result.limit == 50
        assert result.offset == 0

    async def test_passes_sort_parameter(self):
        uow = make_mock_uow()
        review_repo = uow.get_match_review_repository()
        review_repo.list_pending_reviews.return_value = ([], 0)

        command = ListMatchReviewsCommand(sort_by="created_at_desc")
        await ListMatchReviewsUseCase().execute(command, uow)

        review_repo.list_pending_reviews.assert_called_once_with(
            limit=50, offset=0, sort_by="created_at_desc"
        )


class TestListMatchReviewsEmpty:
    """Empty results return zero-count response."""

    async def test_returns_empty_when_no_reviews(self):
        uow = make_mock_uow()
        review_repo = uow.get_match_review_repository()
        review_repo.list_pending_reviews.return_value = ([], 0)

        command = ListMatchReviewsCommand()
        result = await ListMatchReviewsUseCase().execute(command, uow)

        assert result.reviews == []
        assert result.total == 0
