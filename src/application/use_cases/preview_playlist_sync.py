"""Preview the changes a playlist sync would make — pure read-only, no side effects.

Delegates to ``PlaylistReconciliationEngine.preview``, which fetches the real
remote state fresh and diffs it against the canonical (resolving read-only, so a
preview never ingests). This replaces the old preview that diffed against a stale
local cache — and against the canonical itself for push links.
"""

from uuid import UUID

from attrs import define, field

from src.application.services.playlist_reconciliation_engine import (
    PlaylistReconciliationEngine,
)
from src.config import get_logger
from src.domain.entities.playlist_link import SyncDirection
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class PreviewPlaylistSyncCommand:
    """Input: which link to preview sync for, with optional direction override."""

    user_id: str
    link_id: UUID
    direction_override: SyncDirection | None = None


@define(frozen=True, slots=True)
class PreviewPlaylistSyncResult:
    """Output: what the sync would change (read-only)."""

    tracks_to_add: int = field(default=0)
    tracks_to_remove: int = field(default=0)
    tracks_unchanged: int = field(default=0)
    direction: SyncDirection = field(default=SyncDirection.PUSH)
    connector_name: str = field(default="")
    playlist_name: str = field(default="")
    has_comparison_data: bool = field(default=True)
    safety_flagged: bool = field(default=False)
    safety_message: str | None = field(default=None)


@define(slots=True)
class PreviewPlaylistSyncUseCase:
    """Preview what a sync would do without executing it."""

    async def execute(
        self, command: PreviewPlaylistSyncCommand, uow: UnitOfWorkProtocol
    ) -> PreviewPlaylistSyncResult:
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
            direction = command.direction_override or link.sync_direction
            canonical = await uow.get_playlist_repository().get_playlist_by_id(
                link.playlist_id, user_id=command.user_id
            )

            engine = PlaylistReconciliationEngine(
                metric_config=MetricConfigProviderImpl()
            )
            plan = await engine.preview(link, direction, uow, user_id=command.user_id)

        return PreviewPlaylistSyncResult(
            tracks_to_add=plan.tracks_to_add,
            tracks_to_remove=plan.tracks_to_remove,
            tracks_unchanged=plan.tracks_unchanged,
            direction=direction,
            connector_name=link.connector_name,
            playlist_name=canonical.name,
            has_comparison_data=True,
            safety_flagged=plan.requires_confirmation,
            safety_message=plan.safety.reason,
        )
