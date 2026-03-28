"""Sync a playlist link — push canonical→external or pull external→canonical.

Reuses existing infrastructure:
- Push: UpdateConnectorPlaylistUseCase (diff engine + Spotify API operations)
- Pull: sync_connector_playlist() + upsert_canonical_playlist() (backup pattern)

The sync direction is determined by the link's configured direction, with an
optional one-time override.
"""

# pyright: reportExplicitAny=false
# Legitimate Any: use case result union types

from typing import Never
from uuid import UUID

from attrs import define, field

from src.application.services.connector_playlist_sync_service import (
    sync_connector_playlist,
)
from src.application.services.playlist_upsert import upsert_canonical_playlist
from src.application.use_cases.update_connector_playlist import (
    UpdateConnectorPlaylistCommand,
    UpdateConnectorPlaylistUseCase,
)
from src.config import get_logger
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection, SyncStatus
from src.domain.exceptions import ConfirmationRequiredError, NotFoundError
from src.domain.playlist.diff_engine import calculate_playlist_diff
from src.domain.playlist.sync_safety import check_sync_safety
from src.domain.repositories.interfaces import UnitOfWorkProtocol

logger = get_logger(__name__)


def _raise_disappeared(link_id: UUID) -> Never:
    raise NotFoundError(f"Playlist link {link_id} disappeared during sync")


@define(frozen=True, slots=True)
class SyncPlaylistLinkCommand:
    """Input for syncing a playlist link."""

    link_id: UUID
    direction_override: SyncDirection | None = None
    confirmed: bool = False


@define(frozen=True, slots=True)
class SyncPlaylistLinkResult:
    """Output: the updated link with sync metrics."""

    link: PlaylistLink
    tracks_added: int = field(default=0)
    tracks_removed: int = field(default=0)


@define(slots=True)
class SyncPlaylistLinkUseCase:
    """Sync a playlist link in its configured (or overridden) direction.

    Push (canonical → external):
        Loads canonical playlist, builds UpdateConnectorPlaylistCommand,
        delegates to UpdateConnectorPlaylistUseCase for diff + API ops.

    Pull (external → canonical):
        Fetches external playlist via connector, upserts canonical playlist.
        Exact pattern from playlist_backup_service.
    """

    async def execute(
        self, command: SyncPlaylistLinkCommand, uow: UnitOfWorkProtocol
    ) -> SyncPlaylistLinkResult:
        async with uow:
            link_repo = uow.get_playlist_link_repository()
            link = await link_repo.get_link(command.link_id)

            if link is None:
                raise NotFoundError(f"Playlist link {command.link_id} not found")

            link_id: UUID = link.id
            direction = command.direction_override or link.sync_direction

            # Mark as syncing
            await link_repo.update_sync_status(link_id, SyncStatus.SYNCING)
            await uow.commit()

        # Run sync outside the initial transaction — push/pull create their own
        try:
            if direction == SyncDirection.PUSH:
                result = await self._push_sync(link, uow, confirmed=command.confirmed)
            else:
                result = await self._pull_sync(link, uow)

            # Update status to synced and re-fetch for fresh state
            async with uow:
                link_repo = uow.get_playlist_link_repository()
                await link_repo.update_sync_status(
                    link_id,
                    SyncStatus.SYNCED,
                    tracks_added=result.tracks_added,
                    tracks_removed=result.tracks_removed,
                )
                await uow.commit()

                updated_link = await link_repo.get_link(command.link_id)
                if updated_link is None:
                    _raise_disappeared(command.link_id)
                return SyncPlaylistLinkResult(
                    link=updated_link,
                    tracks_added=result.tracks_added,
                    tracks_removed=result.tracks_removed,
                )

        except Exception as e:
            # Update status to error
            async with uow:
                link_repo = uow.get_playlist_link_repository()
                await link_repo.update_sync_status(
                    link_id,
                    SyncStatus.ERROR,
                    error=str(e)[:500],
                )
                await uow.commit()
            raise

    async def _push_sync(
        self,
        link: PlaylistLink,
        uow: UnitOfWorkProtocol,
        *,
        confirmed: bool = False,
    ) -> SyncPlaylistLinkResult:
        """Push canonical playlist to external service.

        When ``confirmed`` is False, runs a safety check against the cached
        external playlist. If the diff would remove a destructive number of
        tracks, raises ``ConfirmationRequiredError`` instead of proceeding.
        """
        async with uow:
            playlist_repo = uow.get_playlist_repository()
            playlist = await playlist_repo.get_playlist_by_id(link.playlist_id)

            # Safety check against cached external playlist (no API call)
            if not confirmed:
                external = await playlist_repo.get_playlist_by_connector(
                    link.connector_name,
                    link.connector_playlist_identifier,
                    raise_if_not_found=False,
                )
                if external is not None:
                    diff = calculate_playlist_diff(external, playlist)
                    removes = diff.operation_summary.get("remove", 0)
                    safety = check_sync_safety(
                        removals=removes, total_current=len(external.tracks)
                    )
                    if safety.flagged:
                        raise ConfirmationRequiredError(
                            safety.reason or "Destructive sync requires confirmation",
                            removals=safety.removals,
                            total=safety.total_current,
                            remaining=safety.remaining_after_sync,
                        )

        tracklist = playlist.to_tracklist()

        command = UpdateConnectorPlaylistCommand(
            playlist_id=link.connector_playlist_identifier,
            new_tracklist=tracklist,
            connector=link.connector_name,
        )

        push_result = await UpdateConnectorPlaylistUseCase().execute(command, uow)

        return SyncPlaylistLinkResult(
            link=link,
            tracks_added=push_result.tracks_added,
            tracks_removed=push_result.tracks_removed,
        )

    async def _pull_sync(
        self, link: PlaylistLink, uow: UnitOfWorkProtocol
    ) -> SyncPlaylistLinkResult:
        """Pull external playlist into canonical playlist."""
        from src.infrastructure.connectors._shared.metric_registry import (
            MetricConfigProviderImpl,
        )

        async with uow:
            # Fetch + cache external playlist
            connector_playlist = await sync_connector_playlist(
                link.connector_name, link.connector_playlist_identifier, uow
            )

            # Count tracks before upsert for diff
            playlist_repo = uow.get_playlist_repository()
            existing = await playlist_repo.get_playlist_by_id(link.playlist_id)
            old_count = len(existing.tracks)

            # Upsert canonical playlist from external data
            await upsert_canonical_playlist(
                connector_playlist,
                link.connector_name,
                link.connector_playlist_identifier,
                uow,
                metric_config=MetricConfigProviderImpl(),
            )

            await uow.commit()

            # Re-fetch for new count
            updated = await playlist_repo.get_playlist_by_id(link.playlist_id)
            new_count = len(updated.tracks)

            added = max(0, new_count - old_count)
            removed = max(0, old_count - new_count)

            return SyncPlaylistLinkResult(
                link=link,
                tracks_added=added,
                tracks_removed=removed,
            )
