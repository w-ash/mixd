"""Preview the changes a playlist sync would make — pure read-only, no side effects.

Uses the real diff engine (same as push sync) when a locally-cached copy of the
external playlist exists. Falls back to a clear "no comparison data" result for
never-synced links. Never calls external APIs or writes to the database.
"""

from uuid import UUID

from attrs import define, field

from src.config import get_logger
from src.domain.entities.playlist import Playlist
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection
from src.domain.playlist.diff_engine import calculate_playlist_diff
from src.domain.playlist.sync_safety import check_sync_safety
from src.domain.repositories.interfaces import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class PreviewPlaylistSyncCommand:
    """Input: which link to preview sync for, with optional direction override."""

    user_id: str
    link_id: UUID
    direction_override: SyncDirection | None = None


@define(frozen=True, slots=True)
class PreviewPlaylistSyncResult:
    """Output: what the sync would change (read-only).

    When ``has_comparison_data`` is False, the link has never been synced
    and we can't compute a diff — the UI should show a first-sync message.
    """

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
    """Preview what a sync would do without executing it.

    Uses the real diff engine against the locally-cached external playlist
    (from the last sync) for accurate add/remove/unchanged counts. For
    never-synced links, returns a result flagged as ``has_comparison_data=False``
    so the UI can show an appropriate first-sync message.
    """

    async def execute(
        self, command: PreviewPlaylistSyncCommand, uow: UnitOfWorkProtocol
    ) -> PreviewPlaylistSyncResult:
        async with uow:
            from src.application.use_cases._shared.playlist_resolver import (
                require_playlist_link,
            )

            link = await require_playlist_link(
                command.link_id, uow, user_id=command.user_id
            )
            direction = command.direction_override or link.sync_direction

            # Load canonical playlist
            playlist_repo = uow.get_playlist_repository()
            canonical = await playlist_repo.get_playlist_by_id(
                link.playlist_id, user_id=command.user_id
            )

            # Load locally-cached external playlist for real diff
            external = await playlist_repo.get_playlist_by_connector(
                link.connector_name,
                link.connector_playlist_identifier,
                user_id=command.user_id,
                raise_if_not_found=False,
            )

            if external is not None:
                return self._compute_diff_preview(link, direction, canonical, external)

            # Never synced — no comparison data available
            return PreviewPlaylistSyncResult(
                direction=direction,
                connector_name=link.connector_name,
                playlist_name=canonical.name,
                has_comparison_data=False,
            )

    @staticmethod
    def _compute_diff_preview(
        link: PlaylistLink,
        direction: SyncDirection,
        canonical: Playlist,
        external: Playlist,
    ) -> PreviewPlaylistSyncResult:
        """Compute exact diff using the same engine as the real sync.

        For push: external is the current state, canonical is the target.
        For pull: canonical is the current state, external is the target.
        """
        if direction == SyncDirection.PUSH:
            diff = calculate_playlist_diff(external, canonical)
        else:
            diff = calculate_playlist_diff(canonical, external)

        summary = diff.operation_summary
        adds = summary.get("add", 0)
        removes = summary.get("remove", 0)

        # "unchanged" = tracks present in both (regardless of reordering)
        current_count = (
            len(external.tracks)
            if direction == SyncDirection.PUSH
            else len(canonical.tracks)
        )
        unchanged = current_count - removes

        safety = check_sync_safety(removals=removes, total_current=current_count)

        return PreviewPlaylistSyncResult(
            tracks_to_add=adds,
            tracks_to_remove=removes,
            tracks_unchanged=unchanged,
            direction=direction,
            connector_name=link.connector_name,
            playlist_name=canonical.name,
            safety_flagged=safety.flagged,
            safety_message=safety.reason,
        )
