"""Track merge service for handling duplicate canonical tracks.

Service that delegates reference migration and deletion to TrackRepository,
keeping merge orchestration logic separate from SQL-level operations.
"""

from collections.abc import Sequence
from uuid import UUID

from attrs import define

from src.config import get_logger
from src.domain.entities import Track
from src.domain.repositories.resolution import SupersessionEdge
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)


@define
class TrackMergeService:
    """Merge duplicate canonical tracks by moving all references to winner track."""

    async def merge_tracks(
        self, winner_id: UUID, loser_id: UUID, uow: UnitOfWorkProtocol
    ) -> Track:
        """Move all foreign key references from loser to winner, then hard-delete loser.

        Args:
            winner_id: Track ID that will keep all the references.
            loser_id: Track ID that will be hard-deleted.
            uow: Unit of work for transaction management.

        Returns:
            Winner track after merge.

        Raises:
            ValueError: If tracks are the same or don't exist.
        """
        if winner_id == loser_id:
            raise ValueError("Cannot merge track with itself")

        logger.info(f"Merging tracks: {loser_id} → {winner_id}")

        # Validate tracks exist
        track_repo = uow.get_track_repository()
        winner_track = await track_repo.get_by_id(winner_id)
        _ = await track_repo.get_by_id(loser_id)  # Just verify it exists

        # Move all foreign key references via repository
        await track_repo.move_references_to_track(loser_id, winner_id)
        edges = await track_repo.merge_mappings_to_track(loser_id, winner_id)
        await track_repo.merge_metrics_to_track(loser_id, winner_id)
        # Same transaction as the merge: a supersession the log did not record
        # is a mapping that changed for no reason anyone can reconstruct.
        await self._record_conflations(edges, uow)

        # Hard delete the loser track
        await track_repo.hard_delete_track(loser_id)

        logger.info(f"Successfully merged tracks: {loser_id} → {winner_id}")
        return winner_track

    @staticmethod
    async def _record_conflations(
        edges: Sequence[SupersessionEdge], uow: UnitOfWorkProtocol
    ) -> None:
        """Emit one ``superseded`` event per mapping the merge retired.

        A near pass-through: the edges already carry their own (user_id,
        connector_name) tenancy, so the seam does the grouping a merge can
        span — this used to hand-roll the same group-by the seam now owns.
        """
        if not edges:
            return
        _ = await uow.get_resolution_recorder().record_supersessions(
            edges, reason="conflation"
        )
