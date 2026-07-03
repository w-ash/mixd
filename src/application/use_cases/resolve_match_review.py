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
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class ResolveMatchReviewCommand:
    """Input parameters for resolving a single match review."""

    user_id: str
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

        review = await review_repo.get_review_by_id(
            command.review_id, user_id=command.user_id
        )
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
            track = await track_repo.get_track_by_id(
                review.track_id, user_id=command.user_id
            )

            # The isrc_suspect flow defers the incoming track to its own
            # canonical instead of merging (v0.8.18 epic 3). Accepting the
            # review means "same recording" — fold that deferred canonical
            # into the reviewed one, unless a manual mapping pins it apart.
            await self._merge_deferred_canonical(
                review, ct.connector_track_identifier, uow, user_id=command.user_id
            )

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

    @staticmethod
    async def _merge_deferred_canonical(
        review: MatchReview,
        connector_track_identifier: str,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> None:
        """Fold a deferred canonical into the reviewed one on accept.

        When the connector track is already mapped to a different canonical
        (created by the suspect-ISRC deferral), accepting asserts the two are
        the same recording: merge it into the reviewed track. A canonical
        holding any manual-override mapping is left alone — the pinned
        mapping is simply re-pointed by the caller.
        """
        connector_repo = uow.get_connector_repository()
        existing = await connector_repo.find_tracks_by_connectors(
            [(review.connector_name, connector_track_identifier)], user_id=user_id
        )
        other = existing.get((review.connector_name, connector_track_identifier))
        if other is None or other.id == review.track_id:
            return

        mappings = await connector_repo.get_full_mappings_for_track(
            other.id, user_id=user_id
        )
        if any(m["origin"] == MappingOrigin.MANUAL_OVERRIDE for m in mappings):
            logger.info(
                "Deferred canonical has manual mappings — re-pointing only",
                deferred_track_id=other.id,
                winner_track_id=review.track_id,
            )
            return

        _ = await uow.get_track_merge_service().merge_tracks(
            review.track_id, other.id, uow
        )
        logger.info(
            "Merged deferred canonical into reviewed track",
            deferred_track_id=other.id,
            winner_track_id=review.track_id,
            connector=review.connector_name,
        )
