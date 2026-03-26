"""Repository for match review queue operations.

Handles persistence of proposed track-to-connector matches awaiting human
review, including listing pending reviews and resolving (accept/reject).
"""

# pyright: reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownMemberType=false
# Legitimate: SQLAlchemy JSON columns produce unknown types for artists field

from datetime import UTC, datetime

import attrs
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import get_logger
from src.config.constants import ReviewStatus
from src.domain.entities.match_review import MatchReview
from src.infrastructure.persistence.database.db_models import (
    DBMatchReview,
)
from src.infrastructure.persistence.repositories.base_repo import (
    BaseRepository,
    SimpleMapperFactory,
)
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)

MatchReviewMapper = SimpleMapperFactory.create(DBMatchReview, MatchReview)


class MatchReviewRepository(BaseRepository[DBMatchReview, MatchReview]):
    """Repository for match review queue operations."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(
            session=session,
            model_class=DBMatchReview,
            mapper=MatchReviewMapper(),
        )

    @db_operation("list_pending_reviews")
    async def list_pending_reviews(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "confidence_desc",
    ) -> tuple[list[MatchReview], int]:
        """List pending reviews with pagination and sorting.

        Returns:
            Tuple of (reviews, total_count).
        """
        base_filter = [DBMatchReview.status == ReviewStatus.PENDING]

        # Count total
        count_stmt = select(func.count(DBMatchReview.id)).where(*base_filter)
        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar_one()

        # Build query with connector track joined for display fields
        stmt = (
            select(DBMatchReview)
            .where(*base_filter)
            .options(selectinload(DBMatchReview.connector_track))
        )

        # Apply sorting
        match sort_by:
            case "confidence_desc":
                stmt = stmt.order_by(
                    DBMatchReview.confidence.desc(), DBMatchReview.id.desc()
                )
            case "confidence_asc":
                stmt = stmt.order_by(
                    DBMatchReview.confidence.asc(), DBMatchReview.id.asc()
                )
            case "created_at_desc":
                stmt = stmt.order_by(
                    DBMatchReview.created_at.desc(), DBMatchReview.id.desc()
                )
            case "created_at_asc":
                stmt = stmt.order_by(
                    DBMatchReview.created_at.asc(), DBMatchReview.id.asc()
                )
            case _:
                stmt = stmt.order_by(
                    DBMatchReview.confidence.desc(), DBMatchReview.id.desc()
                )

        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        db_reviews = list(result.scalars().all())

        # Map to domain with denormalized display fields from connector_track
        reviews: list[MatchReview] = []
        for db_review in db_reviews:
            review = await self.mapper.to_domain(db_review)
            # Enrich with connector track display data
            ct = db_review.connector_track
            artists_raw = ct.artists or {}
            artist_names = _extract_artist_names(artists_raw)
            review = attrs.evolve(
                review,
                connector_track_title=ct.title,
                connector_track_artists=artist_names,
            )
            reviews.append(review)

        return reviews, total

    @db_operation("get_review_by_id")
    async def get_review_by_id(self, review_id: int) -> MatchReview | None:
        """Get a single review by ID."""
        return await self.find_one_by({"id": review_id})

    @db_operation("create_review")
    async def create_review(self, review: MatchReview) -> MatchReview:
        """Create a new match review entry.

        Uses upsert to avoid duplicates on (track_id, connector_name, connector_track_id).
        """
        return await self.upsert(
            lookup_attrs={
                "track_id": review.track_id,
                "connector_name": review.connector_name,
                "connector_track_id": review.connector_track_id,
            },
            create_attrs={
                "match_method": review.match_method,
                "confidence": review.confidence,
                "match_weight": review.match_weight,
                "confidence_evidence": review.confidence_evidence,
                "status": review.status,
            },
        )

    @db_operation("create_reviews_batch")
    async def create_reviews_batch(self, reviews: list[MatchReview]) -> int:
        """Create multiple review entries, skipping duplicates.

        Returns:
            Number of reviews created.
        """
        if not reviews:
            return 0

        entities = [
            {
                "user_id": r.user_id,
                "track_id": r.track_id,
                "connector_name": r.connector_name,
                "connector_track_id": r.connector_track_id,
                "match_method": r.match_method,
                "confidence": r.confidence,
                "match_weight": r.match_weight,
                "confidence_evidence": r.confidence_evidence,
                "status": r.status,
            }
            for r in reviews
        ]

        return await self.bulk_upsert(
            entities=entities,
            lookup_keys=["user_id", "track_id", "connector_name", "connector_track_id"],
            return_models=False,
        )

    @db_operation("update_review_status")
    async def update_review_status(self, review_id: int, status: str) -> MatchReview:
        """Update a review's status (accept/reject)."""
        updates: dict[str, object] = {"status": status}
        if status in (ReviewStatus.ACCEPTED, ReviewStatus.REJECTED):
            updates["reviewed_at"] = datetime.now(UTC)
        return await self.update(review_id, updates)

    @db_operation("count_pending")
    async def count_pending(self) -> int:
        """Count pending reviews."""
        stmt = select(func.count(DBMatchReview.id)).where(
            DBMatchReview.status == ReviewStatus.PENDING
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    @db_operation("count_stale_pending")
    async def count_stale_pending(self, older_than_days: int) -> int:
        """Count pending reviews older than the given threshold."""
        from datetime import timedelta

        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        stmt = select(func.count(DBMatchReview.id)).where(
            DBMatchReview.status == ReviewStatus.PENDING,
            DBMatchReview.created_at < cutoff,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()


def _extract_artist_names(artists_data: object) -> list[str]:
    """Extract artist name strings from JSON-stored artists field.

    Handles both formats: {"names": ["A", "B"]} and [{"name": "A"}, ...].
    """
    if isinstance(artists_data, dict):
        names = artists_data.get("names", [])
        if isinstance(names, list):
            return [str(n) for n in names]
    if isinstance(artists_data, list):
        return [
            str(a["name"]) if isinstance(a, dict) and "name" in a else str(a)
            for a in artists_data
        ]
    return []
