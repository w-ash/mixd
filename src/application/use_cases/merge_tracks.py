"""Use case for merging duplicate tracks.

Moves all references (plays, likes, playlist entries) from the loser track
to the winner track, then soft-deletes the loser. Replaces the thin
track_service.merge_tracks() function with proper Command/Result pattern.
"""

from uuid import UUID

from attrs import define

from src.domain.entities.track import Track
from src.domain.repositories.interfaces import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class MergeTracksCommand:
    """Parameters for merging two duplicate tracks."""

    winner_id: UUID
    loser_id: UUID


@define(frozen=True, slots=True)
class MergeTracksResult:
    """Result of a successful track merge."""

    merged_track: Track


@define(slots=True)
class MergeTracksUseCase:
    """Merge two tracks by moving all references to the winner."""

    async def execute(
        self, command: MergeTracksCommand, uow: UnitOfWorkProtocol
    ) -> MergeTracksResult:
        """Execute the track merge operation.

        Args:
            command: Contains winner_id and loser_id.
            uow: Unit of work for repository and service access.

        Returns:
            MergeTracksResult containing the merged winner track.

        Raises:
            ValueError: If winner_id equals loser_id.
            NotFoundError: If either track does not exist.
        """
        if command.winner_id == command.loser_id:
            raise ValueError("Cannot merge a track with itself")

        async with uow:
            merge_service = uow.get_track_merge_service()
            merged_track = await merge_service.merge_tracks(
                command.winner_id, command.loser_id, uow
            )
            await uow.commit()
            return MergeTracksResult(merged_track=merged_track)
