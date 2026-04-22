"""Refresh the DBConnectorPlaylist cache for a batch of external playlists.

Bounded-concurrent fetch + sequential upsert; never creates canonical
Playlists or PlaylistLinks. Use this when a caller needs fresh cached
playlist state but does not want to fork the playlist into Mixd.

Delegates to ``ensure_connector_playlist_cache`` — the Command side of
the CQS split. This use case's job is to expose that capability to the
API / CLI boundary with Command/Result types.
"""

from collections.abc import Sequence

from attrs import define

from src.application.services.connector_playlist_sync_service import (
    RefreshFailure,
    ensure_connector_playlist_cache,
)
from src.domain.repositories import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class RefreshConnectorPlaylistsCommand:
    user_id: str
    connector_name: str
    connector_playlist_ids: Sequence[str]


@define(frozen=True, slots=True)
class RefreshConnectorPlaylistsResult:
    """Metric-only result — callers who want the playlist data should use
    the Query path (``get_current_connector_playlists``) instead."""

    succeeded: Sequence[str]
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
            outcome = await ensure_connector_playlist_cache(
                command.connector_name,
                command.connector_playlist_ids,
                uow,
            )
            if outcome.fetched:
                await uow.commit()
            return RefreshConnectorPlaylistsResult(
                succeeded=outcome.fetched,
                skipped_unchanged=outcome.cache_hit,
                failed=outcome.failed,
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
