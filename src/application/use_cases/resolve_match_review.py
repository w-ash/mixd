"""Accept or reject a match review item.

On accept: creates a real track_mapping via the connector repository,
then marks the review as accepted.
On reject: marks the review as rejected to prevent re-queuing.
"""

from typing import Literal
from uuid import UUID

from attrs import define

from src.config import get_logger
from src.config.constants import MappingOrigin, ReviewStatus
from src.domain.entities.match_review import MatchReview
from src.domain.exceptions import NotFoundError
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class ResolveMatchReviewCommand:
    """Input parameters for resolving a single match review."""

    review_id: UUID
    action: Literal["accept", "reject"]


@define(frozen=True, slots=True)
class ResolveMatchReviewResult:
    """Outcome of resolving a match review."""

    review: MatchReview
    mapping_created: bool = False


@define(slots=True)
class ResolveMatchReviewUseCase:
    """Accept or reject a proposed match review."""

    async def execute(
        self, command: ResolveMatchReviewCommand, uow: UnitOfWorkProtocol
    ) -> ResolveMatchReviewResult:
        review_repo = uow.get_match_review_repository()

        review = await review_repo.get_review_by_id(command.review_id)
        if review is None:
            raise NotFoundError(f"Match review {command.review_id} not found")

        if review.status != ReviewStatus.PENDING:
            raise ValueError(
                f"Review {command.review_id} already resolved as {review.status}"
            )

        mapping_created = False

        if command.action == "accept":
            # Create a real track mapping via the connector repository
            connector_repo = uow.get_connector_repository()
            ct = await connector_repo.get_connector_track_by_id(
                review.connector_track_id
            )
            if ct is None:
                raise NotFoundError(
                    f"Connector track {review.connector_track_id} no longer exists"
                )

            track_repo = uow.get_track_repository()
            track = await track_repo.get_by_id(review.track_id)

            await connector_repo.map_track_to_connector(
                track=track,
                connector=review.connector_name,
                connector_id=ct.connector_track_identifier,
                match_method=review.match_method,
                confidence=review.confidence,
                confidence_evidence=review.confidence_evidence,
                origin=MappingOrigin.MANUAL_OVERRIDE,
            )
            mapping_created = True
            logger.info(
                "Accepted match review — mapping created",
                review_id=command.review_id,
                track_id=review.track_id,
                connector=review.connector_name,
            )

            new_status = ReviewStatus.ACCEPTED
        else:
            new_status = ReviewStatus.REJECTED
            logger.info(
                "Rejected match review",
                review_id=command.review_id,
                track_id=review.track_id,
            )

        updated_review = await review_repo.update_review_status(
            command.review_id, new_status
        )
        await uow.commit()

        return ResolveMatchReviewResult(
            review=updated_review,
            mapping_created=mapping_created,
        )
