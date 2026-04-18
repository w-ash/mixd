"""Refresh the DBConnectorPlaylist cache for a batch of external playlists.

Bounded-concurrent fetch + sequential upsert; never creates canonical
Playlists or PlaylistLinks. Use this when a caller needs fresh cached
playlist state but does not want to fork the playlist into Mixd.
"""

from collections.abc import Sequence

from attrs import define

from src.application.services.connector_playlist_sync_service import (
    RefreshedPlaylist,
    RefreshFailure,
    batch_refresh_connector_playlists,
)
from src.domain.repositories import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class RefreshConnectorPlaylistsCommand:
    user_id: str
    connector_name: str
    connector_playlist_ids: Sequence[str]


@define(frozen=True, slots=True)
class RefreshConnectorPlaylistsResult:
    succeeded: Sequence[RefreshedPlaylist]
    skipped_unchanged: Sequence[str]
    failed: Sequence[RefreshFailure]


@define(slots=True)
class RefreshConnectorPlaylistsUseCase:
    async def execute(
        self,
        command: RefreshConnectorPlaylistsCommand,
        uow: UnitOfWorkProtocol,
    ) -> RefreshConnectorPlaylistsResult:
        async with uow:
            succeeded, skipped, failed = await batch_refresh_connector_playlists(
                command.connector_name,
                command.connector_playlist_ids,
                uow,
            )
            if succeeded:
                await uow.commit()
            return RefreshConnectorPlaylistsResult(
                succeeded=succeeded,
                skipped_unchanged=skipped,
                failed=failed,
            )


async def run_refresh_connector_playlists(
    user_id: str,
    connector_name: str,
    connector_playlist_ids: Sequence[str],
) -> RefreshConnectorPlaylistsResult:
    """Convenience wrapper for route and CLI handlers."""
    from src.application.runner import execute_use_case

    command = RefreshConnectorPlaylistsCommand(
        user_id=user_id,
        connector_name=connector_name,
        connector_playlist_ids=connector_playlist_ids,
    )
    return await execute_use_case(
        lambda uow: RefreshConnectorPlaylistsUseCase().execute(command, uow),
        user_id=user_id,
    )
