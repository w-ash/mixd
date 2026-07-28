"""Accept or reject a match review item.

On accept: creates a real track_mapping via the connector repository,
then marks the review as accepted.
On reject: marks the review as rejected to prevent re-queuing.
"""

from typing import Literal
from uuid import UUID

from attrs import define

from src.application.use_cases._shared.event_log import apply_with_event_log
from src.config import get_logger
from src.config.constants import MappingOrigin, ReviewStatus
from src.domain.entities.match_review import MatchReview
from src.domain.exceptions import NotFoundError
from src.domain.matching.content_digest import connector_side
from src.domain.repositories.resolution import RejectionCandidate, ResolutionDecision
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
        recorder = uow.get_resolution_recorder()
        decisions: list[ResolutionDecision] = []

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
            decisions = await self._remember_human_rejection(
                review, uow, user_id=command.user_id
            )
            logger.info(
                "Rejected match review",
                review_id=command.review_id,
                track_id=review.track_id,
            )

        updated_review = await review_repo.update_review_status(
            command.review_id, new_status
        )
        # The status flip is the change; its events ride the same commit. On
        # accept the list is empty because the mapping write already emitted
        # `manual_override` from the seam — one decision, one event, and the
        # use case does not get to write a second version of it.
        _ = await apply_with_event_log(
            uow,
            changed=True,
            events=recorder.build_events(decisions, user_id=command.user_id),
            add_events=recorder.write_events,
            user_id=command.user_id,
        )

        return ResolveMatchReviewResult(
            review=updated_review,
            mapping_created=mapping_created,
        )

    @staticmethod
    async def _remember_human_rejection(
        review: MatchReview, uow: UnitOfWorkProtocol, *, user_id: str
    ) -> list[ResolutionDecision]:
        """Turn a human "no" into a sticky cannot-link; return its event.

        A person looked at this pair and said they are not the same recording.
        That is the strongest negative evidence mixd will ever hold, so it
        outranks any TTL: the pair comes back only if the matcher version
        changes, either side's metadata changes, or someone explicitly
        un-rejects it. The accept path needs no event of its own — the mapping
        write emits ``manual_override`` from the one seam.
        """
        recorder = uow.get_resolution_recorder()
        decisions = [
            ResolutionDecision(
                event_type="rejected",
                connector_name=review.connector_name,
                connector_track_id=review.connector_track_id,
                track_id=review.track_id,
                confidence=review.confidence,
                zone="review",
                # Every gray-zone match is offered to the queue today, so
                # the candidate's probability of being seen is 1.0.
                selection_probability=1.0,
                payload={"source": "review_queue", "review_id": str(review.id)},
            )
        ]

        connector_repo = uow.get_connector_repository()
        ct = await connector_repo.get_connector_track_by_id(review.connector_track_id)
        if ct is None:
            # The connector track is gone; the event still stands as the record
            # of the verdict, but there is nothing left to key a constraint to.
            return decisions
        try:
            track = await uow.get_track_repository().get_track_by_id(
                review.track_id, user_id=user_id
            )
        except NotFoundError:
            # Mirror the connector-track guard above: the canonical was merged
            # away or deleted between queueing and reviewing. The verdict is
            # still a fact worth logging; there is simply no pair left to
            # constrain, and raising here would turn a stale queue row into a
            # failed review action the user cannot clear.
            logger.warning(
                "Reviewed track no longer exists — recording verdict without a "
                "cannot-link entry",
                review_id=str(review.id),
                track_id=str(review.track_id),
            )
            return decisions
        _ = await recorder.remember_rejections(
            [RejectionCandidate(connector=connector_side(ct), candidate_track=track)],
            user_id=user_id,
            connector_name=review.connector_name,
        )
        return decisions

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
