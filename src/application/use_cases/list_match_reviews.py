"""List pending match reviews for human disambiguation.

Paginated retrieval of medium-confidence matches that need human review,
sorted by confidence to prioritize the most likely correct matches first.
"""

from attrs import define

from src.config import get_logger
from src.domain.entities.match_review import MatchReview
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class ListMatchReviewsCommand:
    """Input parameters for listing match reviews."""

    limit: int = 50
    offset: int = 0
    sort_by: str = "confidence_desc"


@define(frozen=True, slots=True)
class ListMatchReviewsResult:
    """Paginated list of pending match reviews."""

    reviews: list[MatchReview]
    total: int
    limit: int
    offset: int


@define(slots=True)
class ListMatchReviewsUseCase:
    """Retrieve pending match reviews with pagination."""

    async def execute(
        self, command: ListMatchReviewsCommand, uow: UnitOfWorkProtocol
    ) -> ListMatchReviewsResult:
        review_repo = uow.get_match_review_repository()
        reviews, total = await review_repo.list_pending_reviews(
            limit=command.limit,
            offset=command.offset,
            sort_by=command.sort_by,
        )
        return ListMatchReviewsResult(
            reviews=reviews,
            total=total,
            limit=command.limit,
            offset=command.offset,
        )
