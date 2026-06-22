"""The one reconciliation primitive shared by import, pull, and push.

Replaces three divergent paths (the old import/sync/update-connector use cases),
each of which sourced "the external playlist's current state" from the canonical
playlist itself (a self-reference through the mapping) — making the push diff a
no-op and the destructive-push guard dead. Here, every operation:

1. fetches the REAL remote state fresh,
2. builds a plan by comparing CONNECTOR IDENTIFIERS (not canonical track ids, so
   a pull's not-yet-ingested tracks are still counted — the import-no-op fix),
3. gates a destructive change behind confirmation, against that fresh state,
4. applies atomically — pull upserts the canonical (ingesting + preserving
   unresolved positions), push sends minimal ops to the connector — then
5. records the per-link base snapshot for future change detection.

Lean by construction: composes existing building blocks (sync_connector_playlist,
upsert_canonical_playlist, the shared connector_push primitives, the diff/safety
engines) rather than re-implementing them.
"""

from collections.abc import Hashable, Sequence
import hashlib
from uuid import UUID

from attrs import define

from src.application.services.connector_playlist_sync_service import (
    sync_connector_playlist,
)
from src.application.services.connector_push import (
    external_as_playlist,
    overwrite_external_playlist,
)
from src.application.services.playlist_upsert import upsert_canonical_playlist
from src.application.use_cases._shared.metric_config import MetricConfigProvider
from src.config import get_logger
from src.domain.entities.playlist import ConnectorPlaylist, Playlist
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection
from src.domain.entities.playlist_sync_base import PlaylistSyncBase
from src.domain.entities.shared import ConnectorPlaylistIdentifier
from src.domain.exceptions import ConfirmationRequiredError
from src.domain.playlist.reconciliation import SyncPlan, build_sync_plan
from src.domain.repositories.playlist import PlaylistSyncBaseRepositoryProtocol
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class ReconcileResult:
    """Outcome of an apply: the executed plan's churn + whether it was a no-op."""

    direction: SyncDirection
    tracks_added: int = 0
    tracks_removed: int = 0
    tracks_moved: int = 0  # push: tracks reordered in place (a reorder-only push
    # is a real change even though added/removed are both 0)
    unresolved: int = 0  # pull: canonical positions still unresolved after ingest
    resolved: int = 0  # pull: canonical positions resolved to a track after ingest
    tracks_dropped: int = 0  # push: canonical tracks with no connector match
    skipped: bool = False  # remote already in sync — nothing applied

    @property
    def unmatched(self) -> int:
        """Tracks this sync couldn't place: unresolved (pull) or dropped (push)."""
        return self.unresolved + self.tracks_dropped


@define(frozen=True, slots=True)
class SyncPreview:
    """Read-only preview: the plan plus a staleness token for the confirm flow."""

    plan: SyncPlan
    confirm_token: str


def compute_confirm_token(
    *,
    link_id: UUID,
    direction: SyncDirection,
    snapshot_id: str | None,
    plan: SyncPlan,
) -> str:
    """Secret-less staleness hash for the destructive-sync confirm round-trip.

    NOT an anti-forgery token — the user can already self-confirm and RLS scopes
    their own data. It only proves the plan being confirmed is the plan that was
    previewed: it changes when the remote changes (``snapshot_id``) or the diff
    changes (adds / removes / no-op), so a stale confirmation is rejected and the
    user re-confirms against current reality.
    """
    raw = (
        f"{link_id}|{direction.value}|{snapshot_id}|"
        f"{plan.tracks_to_add}|{plan.tracks_to_remove}|{plan.is_noop}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


@define(slots=True)
class PlaylistReconciliationEngine:
    """Preview and apply a playlist sync in a given direction."""

    metric_config: MetricConfigProvider

    async def preview(
        self,
        link: PlaylistLink,
        direction: SyncDirection,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> SyncPreview:
        """Read-only: fetch fresh remote, return what a sync would change + token.

        Pure identifier comparison — no track resolution or ingest — so a preview
        never mutates the user's data. The ``confirm_token`` pins this exact plan
        for the destructive-sync confirm round-trip.
        """
        canonical = await uow.get_playlist_repository().get_playlist_by_id(
            link.playlist_id, user_id=user_id
        )
        remote_cp = await self._fetch_remote(link, uow)
        plan = await self._build_plan(
            direction, canonical, remote_cp, uow, user_id=user_id
        )
        token = compute_confirm_token(
            link_id=link.id,
            direction=direction,
            snapshot_id=remote_cp.snapshot_id,
            plan=plan,
        )
        return SyncPreview(plan=plan, confirm_token=token)

    async def apply(
        self,
        link: PlaylistLink,
        direction: SyncDirection,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        confirmed: bool = False,
    ) -> ReconcileResult:
        """Fetch fresh remote, gate safety, apply atomically, record the base.

        Connector failures propagate as ``ConnectorSyncError`` (the caller routes
        them to ``SyncStatus.ERROR`` — never a silent SYNCED). A destructive diff
        raises ``ConfirmationRequiredError`` unless ``confirmed``.
        """
        canonical = await uow.get_playlist_repository().get_playlist_by_id(
            link.playlist_id, user_id=user_id
        )
        remote_cp = await self._fetch_remote(link, uow)
        base_repo = uow.get_playlist_sync_base_repository()
        plan = await self._build_plan(
            direction, canonical, remote_cp, uow, user_id=user_id
        )

        if plan.requires_confirmation and not confirmed:
            raise ConfirmationRequiredError(
                plan.safety.reason or "Destructive sync requires confirmation",
                removals=plan.safety.removals,
                total=plan.safety.total_current,
                remaining=plan.safety.remaining_after_sync,
            )

        if plan.is_noop:
            # Nothing to apply — refresh the base snapshot to the current remote so
            # the next sync's change-detection has the latest snapshot.
            await self._record_base(
                link, base_repo, remote_cp.snapshot_id, user_id=user_id
            )
            return ReconcileResult(direction=direction, skipped=True)

        # Each path returns its result plus the post-apply base snapshot (the
        # external state the link now agrees with): the fresh remote for a pull,
        # the post-write snapshot for a push.
        if direction == SyncDirection.PULL:
            result, base_snapshot = await self._apply_pull(
                link, remote_cp, plan, uow, user_id=user_id
            )
        else:
            result, base_snapshot = await self._apply_push(
                link, canonical, remote_cp, uow, user_id=user_id
            )

        await self._record_base(link, base_repo, base_snapshot, user_id=user_id)
        return result

    # -- internals ---------------------------------------------------------

    async def _fetch_remote(
        self, link: PlaylistLink, uow: UnitOfWorkProtocol
    ) -> ConnectorPlaylist:
        """Fetch the live external playlist (and refresh its cache row)."""
        return await sync_connector_playlist(
            link.connector_name,
            ConnectorPlaylistIdentifier(link.connector_playlist_identifier),
            uow,
        )

    @staticmethod
    async def _build_plan(
        direction: SyncDirection,
        canonical: Playlist,
        remote_cp: ConnectorPlaylist,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> SyncPlan:
        # Compare connector identifiers (not canonical track ids), so a pull's
        # not-yet-ingested remote tracks are counted as adds rather than skipped.
        current_ids: Sequence[Hashable]
        target_ids: Sequence[Hashable]
        if direction == SyncDirection.PUSH:
            # Diff the SAME sets the executor does. overwrite_external_playlist
            # diffs the RESOLVED remote (external_as_playlist drops unmatched
            # remote tracks from `.tracks`, so the push never touches them) against
            # the resolved canonical. Counting raw remote ids on the current side
            # would report phantom removals for local/unavailable remote tracks the
            # push leaves in place — inflating the count and tripping the
            # destructive gate. Both sides are resolved-only. (The apply re-resolves
            # for the actual diff; threading one Playlist through preview+apply would
            # need lint-forbidden narrowing, so each path resolves what it needs.)
            external = await external_as_playlist(remote_cp, uow, user_id=user_id)
            current_ids = _resolved_connector_ids(external, remote_cp.connector_name)
            target_ids = _resolved_connector_ids(canonical, remote_cp.connector_name)
        else:
            # A pull overwrites the canonical. Every canonical position counts
            # toward the size and (if absent from the remote) as a removal — incl.
            # cross-matched tracks with no id on this connector — so the
            # destructive-pull gate can't be diluted by positions it would drop.
            current_ids = _canonical_pull_ids(canonical, remote_cp.connector_name)
            target_ids = [item.connector_track_identifier for item in remote_cp.items]
        return build_sync_plan(
            direction=direction, current_ids=current_ids, target_ids=target_ids
        )

    async def _apply_pull(
        self,
        link: PlaylistLink,
        remote_cp: ConnectorPlaylist,
        plan: SyncPlan,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> tuple[ReconcileResult, str | None]:
        """Overwrite the canonical playlist from the fresh remote state.

        New base snapshot = the fetched remote's (canonical now matches it).
        """
        await upsert_canonical_playlist(
            remote_cp,
            link.connector_name,
            link.connector_playlist_identifier,
            uow,
            metric_config=self.metric_config,
            user_id=user_id,
        )
        # Actual unresolved count from the persisted canonical (ingest may leave
        # local/unavailable tracks unresolved).
        updated = await uow.get_playlist_repository().get_playlist_by_id(
            link.playlist_id, user_id=user_id
        )
        result = ReconcileResult(
            direction=SyncDirection.PULL,
            tracks_added=plan.tracks_to_add,
            tracks_removed=plan.tracks_to_remove,
            unresolved=updated.unresolved_count,
            resolved=len(updated.tracks),
        )
        return result, remote_cp.snapshot_id

    @staticmethod
    async def _apply_push(
        link: PlaylistLink,
        canonical: Playlist,
        remote_cp: ConnectorPlaylist,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> tuple[ReconcileResult, str | None]:
        """Push canonical to the external via the shared overwrite primitive.

        New base snapshot = the post-write snapshot (the external now matches
        canonical).
        """
        external = await external_as_playlist(remote_cp, uow, user_id=user_id)
        push = await overwrite_external_playlist(
            link.connector_name,
            ConnectorPlaylistIdentifier(link.connector_playlist_identifier),
            external,
            canonical,
            uow,
        )
        result = ReconcileResult(
            direction=SyncDirection.PUSH,
            tracks_added=push.tracks_added,
            tracks_removed=push.tracks_removed,
            tracks_moved=push.tracks_moved,
            tracks_dropped=push.tracks_dropped,
        )
        return result, push.snapshot_id

    @staticmethod
    async def _record_base(
        link: PlaylistLink,
        base_repo: PlaylistSyncBaseRepositoryProtocol,
        base_snapshot_id: str | None,
        *,
        user_id: str,
    ) -> None:
        """Record the snapshot the link now agrees with.

        Recorded on every apply, but not yet read for planning — preview/apply
        diff fresh remote against canonical directly. This is the forward-looking
        hook Phase 5's snapshot fast-skip + bidirectional merge will consume.
        """
        await base_repo.upsert(
            PlaylistSyncBase(
                link_id=link.id,
                user_id=user_id,
                connector_name=link.connector_name,
                connector_playlist_identifier=link.connector_playlist_identifier,
                base_snapshot_id=base_snapshot_id,
            )
        )


def _resolved_connector_ids(playlist: Playlist, connector_name: str) -> list[str]:
    """Ordered connector ids of the playlist's RESOLVED tracks for this connector.

    Only resolved tracks carry a connector id and only they can be pushed, so both
    sides of a PUSH diff use this — matching the executor, which ignores unresolved
    positions. A resolved track with no id for this connector can't be pushed and
    is omitted.
    """
    ids: list[str] = []
    for entry in playlist.entries:
        if entry.track is not None:
            cid = entry.track.connector_track_identifiers.get(connector_name)
            if cid:
                ids.append(cid)
    return ids


def _canonical_pull_ids(canonical: Playlist, connector_name: str) -> list[Hashable]:
    """Per-position identity of the canonical for a PULL diff against the remote.

    Each position contributes the id that could match a remote item — a resolved
    track's connector id, or an unresolved entry's source id — or, when it has no
    id on this connector (a cross-matched track or a foreign unresolved ref), its
    own entry id (a UUID, which can never equal a connector-id string). The
    fallback keeps such a position counted toward the canonical size AND seen as a
    removal (it can't appear in the remote), so the destructive-pull gate can't be
    diluted by positions the pull would silently drop.
    """
    ids: list[Hashable] = []
    for entry in canonical.entries:
        if entry.track is not None:
            cid = entry.track.connector_track_identifiers.get(connector_name)
            ids.append(cid or entry.id)
        elif (
            ref := entry.connector_track_ref
        ) is not None and ref.connector_name == connector_name:
            ids.append(ref.connector_track_identifier)
        else:
            ids.append(entry.id)
    return ids
