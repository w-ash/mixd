"""Read the negative cache — what is currently being withheld, and about which track.

The read half of the surface whose write half is
``unreject_mapping_candidate``: one listing serves both mechanisms, because a
suppressed pair and an identifier that stopped answering pose the same question
to the same reader. The CLI's ``mixd tracks rejections`` / ``dead-ids`` are the
callers today; the v0.14.0 Manual Mapping UI is the next one.
"""

from collections.abc import Sequence
from uuid import UUID

from attrs import define

from src.domain.repositories.resolution import NegativeListing, NegativeListingKind
from src.domain.repositories.uow import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class ListResolutionNegativesCommand:
    """Which half of the cache to read, and optionally which tracks to narrow to."""

    user_id: str
    kind: NegativeListingKind
    connector_track_ids: Sequence[UUID] | None = None


@define(frozen=True, slots=True)
class ListResolutionNegativesResult:
    """The rows, identity already joined in — no per-row follow-up lookup."""

    listings: list[NegativeListing]


@define(slots=True)
class ListResolutionNegativesUseCase:
    """List un-withdrawn rejections, or identifiers that look dead."""

    async def execute(
        self,
        command: ListResolutionNegativesCommand,
        uow: UnitOfWorkProtocol,
    ) -> ListResolutionNegativesResult:
        async with uow:
            negative_repo = uow.get_resolution_negative_repository()
            listings = await negative_repo.list_negatives(
                user_id=command.user_id,
                kind=command.kind,
                connector_track_ids=command.connector_track_ids,
            )
            return ListResolutionNegativesResult(listings=listings)


async def run_list_resolution_negatives(
    user_id: str,
    kind: NegativeListingKind,
    connector_track_ids: Sequence[UUID] | None = None,
) -> ListResolutionNegativesResult:
    """List negative-cache rows via execute_use_case."""
    from src.application.runner import execute_use_case

    command = ListResolutionNegativesCommand(
        user_id=user_id, kind=kind, connector_track_ids=connector_track_ids
    )
    return await execute_use_case(
        lambda uow: ListResolutionNegativesUseCase().execute(command, uow),
        user_id=user_id,
    )
