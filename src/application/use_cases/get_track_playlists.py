"""Use case for fetching playlists that contain a specific track.

Encapsulates the existence check + playlist lookup that was previously
inlined in the tracks route handler.
"""

from uuid import UUID

from attrs import define

from src.domain.entities import Playlist
from src.domain.repositories.interfaces import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class GetTrackPlaylistsCommand:
    track_id: UUID


@define(frozen=True, slots=True)
class GetTrackPlaylistsResult:
    playlists: list[Playlist]


@define(slots=True)
class GetTrackPlaylistsUseCase:
    async def execute(
        self, command: GetTrackPlaylistsCommand, uow: UnitOfWorkProtocol
    ) -> GetTrackPlaylistsResult:
        track_id = command.track_id
        async with uow:
            # Verify track exists (raises NotFoundError if not)
            await uow.get_track_repository().get_by_id(track_id)
            playlists = await uow.get_playlist_repository().get_playlists_for_track(
                track_id
            )
            return GetTrackPlaylistsResult(playlists=playlists)
