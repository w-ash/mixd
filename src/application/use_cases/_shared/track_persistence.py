"""Shared track persistence logic for use cases.

Ensures tracks have database IDs before being referenced by playlists
or other entities. Tracks that already have IDs pass through unchanged.
"""

from src.domain.entities.track import Track
from src.domain.repositories import UnitOfWorkProtocol


async def persist_unsaved_tracks(
    tracks: list[Track],
    uow: UnitOfWorkProtocol,
) -> list[Track]:
    """Persist tracks that don't have database IDs.

    Tracks with existing IDs pass through unchanged. Tracks without IDs
    are saved to the database and returned with their new IDs.

    Args:
        tracks: Tracks to ensure are persisted.
        uow: Unit of work for repository access.

    Returns:
        List of tracks, all with database IDs assigned.
    """
    track_repo = uow.get_track_repository()
    result: list[Track] = []
    for track in tracks:
        if track.id is None:
            result.append(await track_repo.save_track(track))
        else:
            result.append(track)
    return result
