"""Import connector playlists into Mixd as canonical Playlists.

Composes the CQS ``get_current_connector_playlists`` query (cache refresh +
network fetch on miss) with ``upsert_canonical_playlist`` (CREATE-or-UPDATE)
plus ``PlaylistLink`` creation. The full "fork into Mixd" flow.

When a ``progress_emitter`` is passed, the use case emits:

- one top-level operation for the batch with the playlist count as total
- one sub-operation per playlist with ``phase`` / ``outcome`` / per-page
  track counts in the event metadata, so the UI can render per-playlist
  progress (``4,300 of 8,000 tracks from Spotify``) plus a per-row outcome
  list

Zero events fire when no emitter is passed, preserving every existing CLI
and unit-test path unchanged.
"""

from collections.abc import Sequence
from uuid import UUID

from attrs import define

from src.application.connector_protocols import PlaylistFetchProgress
from src.application.services.connector_playlist_sync_service import (
    get_current_connector_playlists,
    has_fresh_cache,
)
from src.application.services.playlist_upsert import upsert_canonical_playlist
from src.application.services.progress_manager import AsyncProgressManager
from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistResult,
)
from src.application.workflows.protocols import MetricConfigProvider
from src.config import get_logger
from src.domain.entities import ConnectorPlaylist
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection, SyncStatus
from src.domain.entities.progress import (
    OperationStatus,
    ProgressEmitter,
    ProgressOperation,
    ProgressStatus,
    create_progress_event,
    create_progress_operation,
)
from src.domain.entities.shared import JsonValue
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class CanonicalImportOutcome:
    connector_playlist_identifier: str
    canonical_playlist_id: UUID
    resolved: int
    unresolved: int


@define(frozen=True, slots=True)
class CanonicalImportFailure:
    connector_playlist_identifier: str
    message: str


@define(frozen=True, slots=True)
class ImportConnectorPlaylistsAsCanonicalCommand:
    user_id: str
    connector_name: str
    connector_playlist_ids: Sequence[str]
    sync_direction: SyncDirection = SyncDirection.PULL


@define(frozen=True, slots=True)
class ImportConnectorPlaylistsAsCanonicalResult:
    succeeded: Sequence[CanonicalImportOutcome]
    skipped_unchanged: Sequence[str]
    failed: Sequence[CanonicalImportFailure]


def _placeholder_name(cached: dict[str, ConnectorPlaylist], cid: str) -> str:
    """Best-guess display name before the fetch resolves real metadata."""
    cp = cached.get(cid)
    return cp.name if cp is not None else f"Playlist {cid[:8]}"


@define(slots=True)
class ImportConnectorPlaylistsAsCanonicalUseCase:
    metric_config: MetricConfigProvider

    async def execute(
        self,
        command: ImportConnectorPlaylistsAsCanonicalCommand,
        uow: UnitOfWorkProtocol,
        progress_emitter: ProgressEmitter | None = None,
        progress_manager: AsyncProgressManager | None = None,
    ) -> ImportConnectorPlaylistsAsCanonicalResult:
        async with uow:
            link_repo = uow.get_playlist_link_repository()
            cp_repo = uow.get_connector_playlist_repository()

            existing_by_id: dict[str, PlaylistLink] = {
                link.connector_playlist_identifier: link
                for link in await link_repo.list_by_user_connector(
                    command.user_id, command.connector_name
                )
            }
            cached_by_id: dict[str, ConnectorPlaylist] = {
                cp.connector_playlist_identifier: cp
                for cp in await cp_repo.list_by_connector(command.connector_name)
            }

            unique_ids = list(dict.fromkeys(command.connector_playlist_ids))
            link_skipped: list[str] = []
            to_refresh: list[str] = []
            for cid in unique_ids:
                if cid in existing_by_id and has_fresh_cache(cached_by_id, cid):
                    link_skipped.append(cid)
                else:
                    to_refresh.append(cid)

            # Progress emission is optional: when no emitter is passed the
            # coordinator collapses to no-ops so unit tests + CLI paths run
            # identically to before.
            top_op_id = await self._start_top_op(
                progress_emitter, command.connector_name, len(unique_ids)
            )
            sub_op_by_cid: dict[str, str] = {}
            playlist_name_by_cid: dict[str, str] = {}
            parent_completed = 0

            async def _tick_top(message: str) -> None:
                nonlocal parent_completed
                parent_completed += 1
                if progress_emitter is None or top_op_id is None:
                    return
                await progress_emitter.emit_progress(
                    create_progress_event(
                        operation_id=top_op_id,
                        current=parent_completed,
                        total=len(unique_ids),
                        message=message,
                    )
                )

            # Pre-announce every playlist as a sub-op. The real name may not
            # be known yet for fetch-required ids — placeholder fills in and
            # the first sub_progress event carries the real name.
            for cid in link_skipped + to_refresh:
                name = _placeholder_name(cached_by_id, cid)
                playlist_name_by_cid[cid] = name
                sub_op_id = await self._start_sub_op(
                    progress_manager,
                    parent_op_id=top_op_id,
                    name=name,
                    total_tracks=None,
                    cid=cid,
                    phase="starting",
                )
                if sub_op_id is not None:
                    sub_op_by_cid[cid] = sub_op_id

            # Instant completion for link-skipped playlists — the cache is
            # fresh AND the canonical link already exists. Emit a terminal
            # sub_progress with outcome=skipped_unchanged, then complete.
            for cid in link_skipped:
                name = playlist_name_by_cid[cid]
                await self._emit_sub_outcome(
                    progress_manager,
                    sub_op_id=sub_op_by_cid.get(cid),
                    cid=cid,
                    name=name,
                    outcome="skipped_unchanged",
                    message=f"'{name}' is already up to date",
                    phase="done",
                    final_status=OperationStatus.COMPLETED,
                )
                await _tick_top(f"Skipped '{name}'")

            # Build the per-page factory — emits `sub_progress` with current
            # track count + playlist name + phase=fetch so the UI can render
            # a filling bar inside the active sub-op row.
            def make_on_page(cid: str) -> PlaylistFetchProgress:
                async def on_page(fetched: int, total: int) -> None:
                    sub_op_id = sub_op_by_cid.get(cid)
                    if progress_manager is None or sub_op_id is None:
                        return
                    name = playlist_name_by_cid[cid]
                    await progress_manager.emit_progress(
                        create_progress_event(
                            operation_id=sub_op_id,
                            current=fetched,
                            total=total if total > 0 else None,
                            message=(
                                f"Fetching '{name}' from {command.connector_name} — "
                                f"{fetched}/{total} tracks"
                            ),
                            phase="fetch",
                            connector_playlist_identifier=cid,
                            playlist_name=name,
                        )
                    )

                return on_page

            def on_page_factory(cid: str) -> PlaylistFetchProgress | None:
                return make_on_page(cid) if cid in sub_op_by_cid else None

            # Query: resolve every id past the link-skip filter to its
            # current ConnectorPlaylist — cache-hit or network-fetched, same
            # contract. Per-page progress emits via the factory above.
            resolved, resolve_failed = await get_current_connector_playlists(
                command.connector_name,
                to_refresh,
                uow,
                cached_by_id=cached_by_id,
                on_page_factory=on_page_factory,
            )

            succeeded: list[CanonicalImportOutcome] = []
            failed: list[CanonicalImportFailure] = []

            # Fetch-phase failures (404 on the connector, network error).
            for f in resolve_failed:
                cid = f.connector_playlist_identifier
                name = playlist_name_by_cid.get(cid, cid)
                failed.append(
                    CanonicalImportFailure(
                        connector_playlist_identifier=cid,
                        message=f.message,
                    )
                )
                await self._emit_sub_outcome(
                    progress_manager,
                    sub_op_id=sub_op_by_cid.get(cid),
                    cid=cid,
                    name=name,
                    outcome="failed",
                    message=f"Failed to fetch '{name}': {f.message}",
                    phase="fetch",
                    error_message=f.message,
                    final_status=OperationStatus.FAILED,
                )
                await _tick_top(f"Failed '{name}'")

            links_to_create: list[PlaylistLink] = []
            for cid, cp in resolved.items():
                # Update the stored name now that we have real metadata.
                playlist_name_by_cid[cid] = cp.name
                try:
                    # Phase transition: fetch → resolve. One lightweight
                    # event so the UI updates the message; resolution
                    # itself is typically sub-second DB batch work.
                    if progress_manager is not None and cid in sub_op_by_cid:
                        await progress_manager.emit_progress(
                            create_progress_event(
                                operation_id=sub_op_by_cid[cid],
                                current=0,
                                total=len(cp.items) if cp.items else None,
                                message=f"Resolving '{cp.name}' in your library...",
                                phase="resolve",
                                connector_playlist_identifier=cid,
                                playlist_name=cp.name,
                            )
                        )

                    upsert_result = await upsert_canonical_playlist(
                        cp,
                        command.connector_name,
                        cid,
                        uow,
                        metric_config=self.metric_config,
                        user_id=command.user_id,
                    )

                    if cid not in existing_by_id:
                        links_to_create.append(
                            PlaylistLink(
                                playlist_id=upsert_result.playlist.id,
                                connector_name=command.connector_name,
                                connector_playlist_identifier=cid,
                                connector_playlist_name=cp.name,
                                sync_direction=command.sync_direction,
                                sync_status=SyncStatus.NEVER_SYNCED,
                            )
                        )

                    resolved_count = len(upsert_result.playlist.tracks)
                    unresolved_count = max(len(cp.items) - resolved_count, 0)
                    succeeded.append(
                        CanonicalImportOutcome(
                            connector_playlist_identifier=cid,
                            canonical_playlist_id=upsert_result.playlist.id,
                            resolved=resolved_count,
                            unresolved=unresolved_count,
                        )
                    )
                    logger.info(
                        "Imported connector playlist as canonical",
                        connector=command.connector_name,
                        connector_playlist_id=cid,
                        playlist_id=upsert_result.playlist.id,
                        resolved=resolved_count,
                        op=(
                            "created"
                            if isinstance(upsert_result, CreateCanonicalPlaylistResult)
                            else "updated"
                        ),
                    )
                    await self._emit_sub_outcome(
                        progress_manager,
                        sub_op_id=sub_op_by_cid.get(cid),
                        cid=cid,
                        name=cp.name,
                        outcome="succeeded",
                        message=(
                            f"Imported '{cp.name}' — {resolved_count} resolved"
                            + (
                                f", {unresolved_count} unresolved"
                                if unresolved_count
                                else ""
                            )
                        ),
                        phase="done",
                        resolved=resolved_count,
                        unresolved=unresolved_count,
                        canonical_playlist_id=str(upsert_result.playlist.id),
                        final_status=OperationStatus.COMPLETED,
                    )
                    await _tick_top(f"Imported '{cp.name}'")
                except Exception as exc:
                    logger.warning(
                        "Failed to create canonical playlist from connector",
                        connector=command.connector_name,
                        connector_playlist_id=cid,
                        exc_info=True,
                    )
                    failed.append(
                        CanonicalImportFailure(
                            connector_playlist_identifier=cid,
                            message=str(exc),
                        )
                    )
                    await self._emit_sub_outcome(
                        progress_manager,
                        sub_op_id=sub_op_by_cid.get(cid),
                        cid=cid,
                        name=cp.name,
                        outcome="failed",
                        message=f"Failed to import '{cp.name}': {exc}",
                        phase="resolve",
                        error_message=str(exc),
                        final_status=OperationStatus.FAILED,
                    )
                    await _tick_top(f"Failed '{cp.name}'")

            if links_to_create:
                await link_repo.create_links_batch(links_to_create)

            if succeeded:
                await uow.commit()

            await self._complete_top_op(
                progress_emitter,
                top_op_id,
                succeeded_count=len(succeeded),
                failed_count=len(failed),
            )

            return ImportConnectorPlaylistsAsCanonicalResult(
                succeeded=succeeded,
                skipped_unchanged=link_skipped,
                failed=failed,
            )

    @staticmethod
    async def _start_top_op(
        emitter: ProgressEmitter | None,
        connector_name: str,
        total: int,
    ) -> str | None:
        if emitter is None:
            return None
        return await emitter.start_operation(
            create_progress_operation(
                description=f"Importing {total} playlists from {connector_name}",
                total_items=total if total > 0 else None,
            )
        )

    @staticmethod
    async def _complete_top_op(
        emitter: ProgressEmitter | None,
        top_op_id: str | None,
        *,
        succeeded_count: int,
        failed_count: int,
    ) -> None:
        if emitter is None or top_op_id is None:
            return
        # FAILED only when every playlist failed; partial success stays
        # COMPLETED so the SSE `complete` event fires (UI then inspects
        # sub-op outcomes for the red/green breakdown).
        final = (
            OperationStatus.FAILED
            if failed_count > 0 and succeeded_count == 0
            else OperationStatus.COMPLETED
        )
        await emitter.complete_operation(top_op_id, final)

    @staticmethod
    async def _start_sub_op(
        manager: AsyncProgressManager | None,
        *,
        parent_op_id: str | None,
        name: str,
        total_tracks: int | None,
        cid: str,
        phase: str,
    ) -> str | None:
        if manager is None or parent_op_id is None:
            return None
        operation = ProgressOperation(
            description=name,
            total_items=total_tracks,
            metadata={
                "parent_operation_id": parent_op_id,
                "phase": phase,
                "connector_playlist_identifier": cid,
                "playlist_name": name,
            },
        )
        return await manager.start_operation(operation)

    @staticmethod
    async def _emit_sub_outcome(
        manager: AsyncProgressManager | None,
        *,
        sub_op_id: str | None,
        cid: str,
        name: str,
        outcome: str,
        message: str,
        phase: str,
        final_status: OperationStatus,
        resolved: int | None = None,
        unresolved: int | None = None,
        canonical_playlist_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if manager is None or sub_op_id is None:
            return
        metadata: dict[str, JsonValue] = {
            "connector_playlist_identifier": cid,
            "playlist_name": name,
            "phase": phase,
            "outcome": outcome,
        }
        if resolved is not None:
            metadata["resolved"] = resolved
        if unresolved is not None:
            metadata["unresolved"] = unresolved
        if canonical_playlist_id is not None:
            metadata["canonical_playlist_id"] = canonical_playlist_id
        if error_message is not None:
            metadata["error_message"] = error_message

        status = (
            ProgressStatus.COMPLETED
            if final_status == OperationStatus.COMPLETED
            else ProgressStatus.FAILED
        )
        await manager.emit_progress(
            create_progress_event(
                operation_id=sub_op_id,
                current=resolved if resolved is not None else 0,
                total=None,
                message=message,
                status=status,
                **metadata,
            )
        )
        await manager.complete_operation(sub_op_id, final_status)


async def run_import_connector_playlists_as_canonical(
    user_id: str,
    connector_name: str,
    connector_playlist_ids: Sequence[str],
    sync_direction: SyncDirection = SyncDirection.PULL,
    progress_emitter: ProgressEmitter | None = None,
    progress_manager: AsyncProgressManager | None = None,
) -> ImportConnectorPlaylistsAsCanonicalResult:
    """Convenience wrapper for route and CLI handlers.

    When called from the REST API, the route passes both the bound emitter
    (for top-level op lifecycle on the pre-assigned operation_id) and the
    ``AsyncProgressManager`` (for sub-op creation with fresh ids that
    inherit the parent queue via metadata). When called from the CLI or
    unit tests, both default to ``None`` and the use case emits zero
    events — keeping the existing CLI + test paths byte-identical.
    """
    from src.application.runner import execute_use_case
    from src.infrastructure.connectors._shared.metric_registry import (
        MetricConfigProviderImpl,
    )

    command = ImportConnectorPlaylistsAsCanonicalCommand(
        user_id=user_id,
        connector_name=connector_name,
        connector_playlist_ids=connector_playlist_ids,
        sync_direction=sync_direction,
    )
    use_case = ImportConnectorPlaylistsAsCanonicalUseCase(
        metric_config=MetricConfigProviderImpl()
    )
    return await execute_use_case(
        lambda uow: use_case.execute(
            command,
            uow,
            progress_emitter=progress_emitter,
            progress_manager=progress_manager,
        ),
        user_id=user_id,
    )
