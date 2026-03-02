"""Simple track read and merge operations."""

from src.domain.entities.track import Track
from src.domain.repositories import UnitOfWorkProtocol


async def get_tracks(uow: UnitOfWorkProtocol, *track_ids: int) -> list[Track]:
    """Fetch multiple tracks by ID."""
    repo = uow.get_track_repository()
    return [await repo.get_by_id(tid) for tid in track_ids]


async def merge_tracks(uow: UnitOfWorkProtocol, winner_id: int, loser_id: int) -> Track:
    """Merge two tracks, moving all references to the winner."""
    return await uow.get_track_merge_service().merge_tracks(winner_id, loser_id, uow)
