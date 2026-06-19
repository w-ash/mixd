"""Match-review repository protocol.

Split from the former monolithic ``interfaces.py``.
"""

from collections.abc import Awaitable
from typing import Protocol
from uuid import UUID

from src.domain.entities.match_review import MatchReview


class MatchReviewRepositoryProtocol(Protocol):
    """Repository interface for match review queue operations."""

    def list_pending_reviews(
        self,
        *,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "confidence_desc",
    ) -> Awaitable[tuple[list[MatchReview], int]]:
        """List pending reviews with pagination and sorting."""
        ...

    def get_review_by_id(
        self, review_id: UUID, *, user_id: str
    ) -> Awaitable[MatchReview | None]:
        """Get a single review by ID, verifying ownership."""
        ...

    def create_review(self, review: MatchReview) -> Awaitable[MatchReview]:
        """Create a new match review entry."""
        ...

    def create_reviews_batch(self, reviews: list[MatchReview]) -> Awaitable[int]:
        """Create multiple review entries, skipping duplicates."""
        ...

    def update_review_status(
        self, review_id: UUID, status: str
    ) -> Awaitable[MatchReview]:
        """Update a review's status (accept/reject)."""
        ...

    def count_pending(self, *, user_id: str) -> Awaitable[int]:
        """Count pending reviews."""
        ...

    def count_stale_pending(
        self, older_than_days: int, *, user_id: str
    ) -> Awaitable[int]:
        """Count pending reviews older than the given threshold."""
        ...
