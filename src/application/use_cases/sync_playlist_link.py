"""Sync a playlist link — push canonical→external or pull external→canonical.

A thin status-lifecycle wrapper over ``PlaylistReconciliationEngine``: it owns the
link's SYNCING → SYNCED / ERROR transitions; the engine does the real work
(fresh fetch → diff vs base → safety gate → atomic apply → record base). The
direction is the link's configured direction, with an optional one-time override.
"""

from typing import Never
from uuid import UUID

from attrs import define, field

from src.application.services.playlist_reconciliation_engine import (
    PlaylistReconciliationEngine,
)
from src.config import get_logger
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection, SyncStatus
from src.domain.exceptions import ConfirmationRequiredError, NotFoundError
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)


def _raise_disappeared(link_id: UUID) -> Never:
    raise NotFoundError(f"Playlist link {link_id} disappeared during sync")


@define(frozen=True, slots=True)
class SyncPlaylistLinkCommand:
    """Input for syncing a playlist link."""

    user_id: str
    link_id: UUID
    direction_override: SyncDirection | None = None
    confirmed: bool = False


@define(frozen=True, slots=True)
class SyncPlaylistLinkResult:
    """Output: the updated link with sync metrics."""

    link: PlaylistLink
    tracks_added: int = field(default=0)
    tracks_removed: int = field(default=0)
    tracks_unmatched: int = field(default=0)  # tracks with no match this sync


@define(slots=True)
class SyncPlaylistLinkUseCase:
    """Sync a playlist link. Push: canonical → external. Pull: external → canonical."""

    async def execute(
        self, command: SyncPlaylistLinkCommand, uow: UnitOfWorkProtocol
    ) -> SyncPlaylistLinkResult:
        from src.application.use_cases._shared.playlist_resolver import (
            require_playlist_link,
        )
        from src.infrastructure.connectors._shared.metric_registry import (
            MetricConfigProviderImpl,
        )

        async with uow:
            link = await require_playlist_link(
                command.link_id, uow, user_id=command.user_id
            )
            link_id = link.id
            direction = command.direction_override or link.sync_direction
            original_status = link.sync_status
            await uow.get_playlist_link_repository().update_sync_status(
                link_id, SyncStatus.SYNCING
            )
            await uow.commit()

        engine = PlaylistReconciliationEngine(metric_config=MetricConfigProviderImpl())
        try:
            return await self._apply(engine, command, uow, link, direction, link_id)
        except ConfirmationRequiredError:
            # The safety gate fired before any change. Restore the prior status so
            # the link isn't stranded in SYNCING, then surface for the confirm flow.
            await self._set_status(uow, link_id, original_status)
            raise
        except Exception as e:
            await self._set_status(uow, link_id, SyncStatus.ERROR, error=str(e)[:500])
            raise

    @staticmethod
    async def _apply(
        engine: PlaylistReconciliationEngine,
        command: SyncPlaylistLinkCommand,
        uow: UnitOfWorkProtocol,
        link: PlaylistLink,
        direction: SyncDirection,
        link_id: UUID,
    ) -> SyncPlaylistLinkResult:
        """Run the engine apply, mark SYNCED, and return the refreshed link."""
        async with uow:
            result = await engine.apply(
                link,
                direction,
                uow,
                user_id=command.user_id,
                confirmed=command.confirmed,
            )
            link_repo = uow.get_playlist_link_repository()
            await link_repo.update_sync_status(
                link_id,
                SyncStatus.SYNCED,
                tracks_added=result.tracks_added,
                tracks_removed=result.tracks_removed,
                tracks_unmatched=result.unmatched,
            )
            await uow.commit()
            updated_link = await link_repo.get_link(link_id)

        if updated_link is None:
            _raise_disappeared(link_id)
        return SyncPlaylistLinkResult(
            link=updated_link,
            tracks_added=result.tracks_added,
            tracks_removed=result.tracks_removed,
            tracks_unmatched=result.unmatched,
        )

    @staticmethod
    async def _set_status(
        uow: UnitOfWorkProtocol,
        link_id: UUID,
        status: SyncStatus,
        *,
        error: str | None = None,
    ) -> None:
        async with uow:
            await uow.get_playlist_link_repository().update_sync_status(
                link_id, status, error=error
            )
            await uow.commit()
