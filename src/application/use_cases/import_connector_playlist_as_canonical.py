"""Import connector playlists into Mixd as canonical Playlists.

Two paths, one batch:

- **First import** (no link yet) — fetch the connector playlist (cache
  read-through, with per-page progress) and CREATE the canonical Playlist +
  ``PlaylistLink``. ``upsert_canonical_playlist`` preserves unresolved positions.
- **Re-import** (a link already exists) — delegate to
  ``PlaylistReconciliationEngine.apply(PULL)``, which fetches the *live* remote
  fresh and reconciles the canonical against it. This is the durability fix: the
  old code short-circuited a re-import to a no-op whenever the cache held a
  snapshot, so a canonical that had diverged from the remote never reconciled.
  The engine always fetches fresh and diffs, so a real change is applied and a
  genuine no-op is reported as ``skipped_unchanged``.

The import *action* is always a pull (external → canonical); the link's standing
``sync_direction`` (used by later interactive syncs) is stamped only on links
created here and never mutated on re-import.

When a ``progress_emitter`` is passed, the use case emits one top-level operation
for the batch plus one sub-operation per playlist (phase / outcome / per-page
counts in event metadata). Zero events fire when no emitter is passed, preserving
every existing CLI and unit-test path unchanged.
"""

from collections.abc import Awaitable, Callable, Sequence
from uuid import UUID

from attrs import define

from src.application.connector_protocols import PlaylistFetchProgress
from src.application.services.connector_playlist_sync_service import (
    get_current_connector_playlists,
)
from src.application.services.operation_run_recorder import append_run_issue
from src.application.services.playlist_reconciliation_engine import (
    PlaylistReconciliationEngine,
)
from src.application.services.playlist_upsert import upsert_canonical_playlist
from src.application.services.progress_broker import ProgressBroker
from src.application.use_cases._shared.metric_config import MetricConfigProvider
from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistResult,
)
from src.config import get_logger
from src.domain.entities import ConnectorPlaylist
from src.domain.entities.operations import OperationResult
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection, SyncStatus
from src.domain.entities.progress import (
    OperationStatus,
    ProgressEmitter,
    ProgressOperation,
    ProgressStatus,
    create_progress_event,
    create_progress_operation,
)
from src.domain.entities.shared import ConnectorPlaylistIdentifier, JsonValue
from src.domain.entities.summary_metrics import SummaryMetricCollection
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class CanonicalImportOutcome:
    connector_playlist_identifier: ConnectorPlaylistIdentifier
    canonical_playlist_id: UUID
    resolved: int
    unresolved: int
    # True when this import CREATED the canonical + link (first import); False
    # when it reconciled an existing link (re-import). Splits the audit row's
    # ``imported`` vs ``updated`` counts; the CLI ignores it.
    was_created: bool = True


@define(frozen=True, slots=True)
class CanonicalImportFailure:
    connector_playlist_identifier: ConnectorPlaylistIdentifier
    message: str


@define(frozen=True, slots=True)
class ImportConnectorPlaylistsAsCanonicalCommand:
    user_id: str
    connector_name: str
    connector_playlist_identifiers: Sequence[ConnectorPlaylistIdentifier]
    # The standing direction stamped on *newly created* links (the import action
    # itself is always a pull). Defaults to PULL: a freshly imported playlist
    # most-likely wants to keep pulling from the connector.
    sync_direction: SyncDirection = SyncDirection.PULL
    # When True, bypass the cache for first imports and fetch fresh. Backs the
    # ``import-spotify --refresh`` CLI flag / web "Force re-fetch" toggle.
    # Re-imports always fetch fresh via the engine regardless of this flag.
    force: bool = False


@define(frozen=True, slots=True)
class ImportConnectorPlaylistsAsCanonicalResult:
    succeeded: Sequence[CanonicalImportOutcome]
    skipped_unchanged: Sequence[str]
    failed: Sequence[CanonicalImportFailure]


def to_operation_result(
    result: ImportConnectorPlaylistsAsCanonicalResult,
) -> OperationResult:
    """Map the native import result onto an ``OperationResult`` for the SSE seam.

    ``launch_sse_operation`` finalizes the ``OperationRun`` audit row + terminal
    event from the use case's returned ``OperationResult`` (``_audit_outcome``).
    Adding an ``errors`` metric on any failure makes ``is_failure`` true — the
    same convention the likes/history imports already use — so a failed import is
    durably recorded as ``error`` with the run's counts.
    """
    imported = sum(1 for o in result.succeeded if o.was_created)
    updated = sum(1 for o in result.succeeded if not o.was_created)
    unresolved = sum(o.unresolved for o in result.succeeded)
    skipped = len(result.skipped_unchanged)
    errors = len(result.failed)

    metrics = SummaryMetricCollection()
    metrics.add("imported", imported, "Playlists Imported", significance=1)
    if updated:
        metrics.add("updated", updated, "Playlists Updated", significance=2)
    if skipped:
        metrics.add("skipped", skipped, "Skipped (Unchanged)", significance=3)
    if unresolved:
        metrics.add("unresolved", unresolved, "Tracks Unresolved", significance=4)
    if errors:
        metrics.add("errors", errors, "Errors", significance=5)

    return OperationResult(
        operation_name="import_connector_playlists",
        summary_metrics=metrics,
    )


@define(slots=True)
class ImportConnectorPlaylistsAsCanonicalUseCase:
    metric_config: MetricConfigProvider

    async def execute(
        self,
        command: ImportConnectorPlaylistsAsCanonicalCommand,
        uow: UnitOfWorkProtocol,
        progress_emitter: ProgressEmitter | None = None,
        progress_broker: ProgressBroker | None = None,
        parent_operation_id: str | None = None,
        run_id: UUID | None = None,
    ) -> ImportConnectorPlaylistsAsCanonicalResult:
        succeeded: list[CanonicalImportOutcome] = []
        skipped_unchanged: list[ConnectorPlaylistIdentifier] = []
        failed: list[CanonicalImportFailure] = []

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

            unique_ids = list(dict.fromkeys(command.connector_playlist_identifiers))
            new_ids = [c for c in unique_ids if c not in existing_by_id]
            existing_ids = [c for c in unique_ids if c in existing_by_id]

            # Progress emission is optional: with no emitter the coordinator
            # collapses to no-ops so unit tests + CLI paths run identically.
            top_op_id = await self._start_top_op(
                progress_emitter,
                command.connector_name,
                len(unique_ids),
                parent_operation_id=parent_operation_id,
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

            # Pre-announce every playlist as a sub-op (new imports first, then
            # re-imports). The real name may not be known yet — placeholder fills
            # in and the first sub_progress event carries the real name.
            for cid in new_ids + existing_ids:
                name = self._announce_name(cid, cached_by_id, existing_by_id)
                playlist_name_by_cid[cid] = name
                sub_op_id = await self._start_sub_op(
                    progress_broker,
                    parent_op_id=top_op_id,
                    name=name,
                    total_tracks=None,
                    cid=cid,
                    phase="starting",
                )
                if sub_op_id is not None:
                    sub_op_by_cid[cid] = sub_op_id

            # ── First imports: fetch (with per-page progress) + create ──────────
            def make_on_page(cid: str) -> PlaylistFetchProgress:
                async def on_page(fetched: int, total: int) -> None:
                    sub_op_id = sub_op_by_cid.get(cid)
                    if progress_broker is None or sub_op_id is None:
                        return
                    name = playlist_name_by_cid[cid]
                    await progress_broker.emit_progress(
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

            resolved, resolve_failed = await get_current_connector_playlists(
                command.connector_name,
                new_ids,
                uow,
                cached_by_id=cached_by_id,
                on_page_factory=on_page_factory,
                force=command.force,
            )

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
                    progress_broker,
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
                playlist_name_by_cid[cid] = cp.name
                try:
                    await self._import_one(
                        cid,
                        cp,
                        command,
                        uow,
                        progress_broker=progress_broker,
                        sub_op_by_cid=sub_op_by_cid,
                        links_to_create=links_to_create,
                        succeeded=succeeded,
                        tick_top=_tick_top,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to create canonical playlist from connector",
                        connector=command.connector_name,
                        connector_playlist_identifier=cid,
                        exc_info=True,
                    )
                    failed.append(
                        CanonicalImportFailure(
                            connector_playlist_identifier=ConnectorPlaylistIdentifier(
                                cid
                            ),
                            message=str(exc),
                        )
                    )
                    await self._emit_sub_outcome(
                        progress_broker,
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

            # ── Re-imports: reconcile each existing link against fresh remote ───
            engine = PlaylistReconciliationEngine(metric_config=self.metric_config)
            for cid in existing_ids:
                link = existing_by_id[cid]
                name = playlist_name_by_cid[cid]
                try:
                    await self._reimport_one(
                        cid,
                        link,
                        name,
                        command,
                        uow,
                        engine,
                        progress_broker=progress_broker,
                        sub_op_by_cid=sub_op_by_cid,
                        succeeded=succeeded,
                        skipped_unchanged=skipped_unchanged,
                        tick_top=_tick_top,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to re-import (reconcile) connector playlist",
                        connector=command.connector_name,
                        connector_playlist_identifier=cid,
                        exc_info=True,
                    )
                    failed.append(
                        CanonicalImportFailure(
                            connector_playlist_identifier=ConnectorPlaylistIdentifier(
                                cid
                            ),
                            message=str(exc),
                        )
                    )
                    await self._emit_sub_outcome(
                        progress_broker,
                        sub_op_id=sub_op_by_cid.get(cid),
                        cid=cid,
                        name=name,
                        outcome="failed",
                        message=f"Failed to update '{name}': {exc}",
                        phase="resolve",
                        error_message=str(exc),
                        final_status=OperationStatus.FAILED,
                    )
                    await _tick_top(f"Failed '{name}'")

            # Commit when anything was processed without failing — a re-import
            # no-op still records a fresh base snapshot (a write), so
            # skipped_unchanged must also drive the commit.
            if succeeded or skipped_unchanged:
                await uow.commit()

            await self._complete_top_op(
                progress_emitter,
                top_op_id,
                succeeded_count=len(succeeded) + len(skipped_unchanged),
                failed_count=len(failed),
                seam_owned=parent_operation_id is not None,
            )

        # Record per-playlist failures on the durable audit row in their own
        # transaction (so they survive even if the batch did not commit), after
        # the batch UoW has closed. No-op when no audit row exists (CLI/tests).
        await self._record_issues(run_id, failed, command.user_id)

        return ImportConnectorPlaylistsAsCanonicalResult(
            succeeded=succeeded,
            skipped_unchanged=skipped_unchanged,
            failed=failed,
        )

    async def _import_one(
        self,
        cid: str,
        cp: ConnectorPlaylist,
        command: ImportConnectorPlaylistsAsCanonicalCommand,
        uow: UnitOfWorkProtocol,
        *,
        progress_broker: ProgressBroker | None,
        sub_op_by_cid: dict[str, str],
        links_to_create: list[PlaylistLink],
        succeeded: list[CanonicalImportOutcome],
        tick_top: Callable[[str], Awaitable[None]],
    ) -> None:
        """CREATE the canonical Playlist + link for a first import."""
        if progress_broker is not None and cid in sub_op_by_cid:
            await progress_broker.emit_progress(
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
                connector_playlist_identifier=ConnectorPlaylistIdentifier(cid),
                canonical_playlist_id=upsert_result.playlist.id,
                resolved=resolved_count,
                unresolved=unresolved_count,
                was_created=True,
            )
        )
        logger.info(
            "Imported connector playlist as canonical",
            connector=command.connector_name,
            connector_playlist_identifier=cid,
            playlist_id=upsert_result.playlist.id,
            resolved=resolved_count,
            op=(
                "created"
                if isinstance(upsert_result, CreateCanonicalPlaylistResult)
                else "updated"
            ),
        )
        await self._emit_sub_outcome(
            progress_broker,
            sub_op_id=sub_op_by_cid.get(cid),
            cid=cid,
            name=cp.name,
            outcome="succeeded",
            message=(
                f"Imported '{cp.name}' — {resolved_count} resolved"
                + (f", {unresolved_count} unresolved" if unresolved_count else "")
            ),
            phase="done",
            resolved=resolved_count,
            unresolved=unresolved_count,
            canonical_playlist_id=str(upsert_result.playlist.id),
            final_status=OperationStatus.COMPLETED,
        )
        await tick_top(f"Imported '{cp.name}'")

    async def _reimport_one(
        self,
        cid: str,
        link: PlaylistLink,
        name: str,
        command: ImportConnectorPlaylistsAsCanonicalCommand,
        uow: UnitOfWorkProtocol,
        engine: PlaylistReconciliationEngine,
        *,
        progress_broker: ProgressBroker | None,
        sub_op_by_cid: dict[str, str],
        succeeded: list[CanonicalImportOutcome],
        skipped_unchanged: list[ConnectorPlaylistIdentifier],
        tick_top: Callable[[str], Awaitable[None]],
    ) -> None:
        """Reconcile an existing link against fresh remote via the engine (PULL).

        The engine fetches the live remote, diffs, and overwrites the canonical.
        ``confirmed=True``: a re-import is an explicit "mirror the external"
        action — the destructive guard belongs to interactive sync, not import.
        """
        if progress_broker is not None and cid in sub_op_by_cid:
            await progress_broker.emit_progress(
                create_progress_event(
                    operation_id=sub_op_by_cid[cid],
                    current=0,
                    total=None,
                    message=f"Reconciling '{name}' with {command.connector_name}...",
                    phase="fetch",
                    connector_playlist_identifier=cid,
                    playlist_name=name,
                )
            )

        result = await engine.apply(
            link,
            SyncDirection.PULL,
            uow,
            user_id=command.user_id,
            confirmed=True,
        )

        if result.skipped:
            skipped_unchanged.append(ConnectorPlaylistIdentifier(cid))
            await self._emit_sub_outcome(
                progress_broker,
                sub_op_id=sub_op_by_cid.get(cid),
                cid=cid,
                name=name,
                outcome="skipped_unchanged",
                message=f"'{name}' is already up to date",
                phase="done",
                final_status=OperationStatus.COMPLETED,
            )
            await tick_top(f"Skipped '{name}'")
            return

        succeeded.append(
            CanonicalImportOutcome(
                connector_playlist_identifier=ConnectorPlaylistIdentifier(cid),
                canonical_playlist_id=link.playlist_id,
                resolved=result.resolved,
                unresolved=result.unresolved,
                was_created=False,
            )
        )
        logger.info(
            "Re-imported connector playlist (reconciled)",
            connector=command.connector_name,
            connector_playlist_identifier=cid,
            playlist_id=link.playlist_id,
            tracks_added=result.tracks_added,
            tracks_removed=result.tracks_removed,
        )
        message = (
            f"Updated '{name}' — {result.tracks_added} added, "
            f"{result.tracks_removed} removed"
            + (f", {result.unresolved} unresolved" if result.unresolved else "")
        )
        await self._emit_sub_outcome(
            progress_broker,
            sub_op_id=sub_op_by_cid.get(cid),
            cid=cid,
            name=name,
            outcome="succeeded",
            message=message,
            phase="done",
            resolved=result.resolved,
            unresolved=result.unresolved,
            canonical_playlist_id=str(link.playlist_id),
            final_status=OperationStatus.COMPLETED,
        )
        await tick_top(f"Updated '{name}'")

    @staticmethod
    async def _record_issues(
        run_id: UUID | None,
        failed: Sequence[CanonicalImportFailure],
        user_id: str,
    ) -> None:
        """Append each per-playlist failure to the durable ``OperationRun`` row."""
        if run_id is None or not failed:
            return
        for f in failed:
            await append_run_issue(
                run_id,
                user_id=user_id,
                issue={
                    "connector_playlist_identifier": str(
                        f.connector_playlist_identifier
                    ),
                    "message": f.message,
                },
            )

    @staticmethod
    def _announce_name(
        cid: str,
        cached: dict[str, ConnectorPlaylist],
        existing: dict[str, PlaylistLink],
    ) -> str:
        """Best-guess display name before the fetch resolves real metadata."""
        link = existing.get(cid)
        if link is not None and link.connector_playlist_name:
            return link.connector_playlist_name
        cp = cached.get(cid)
        return cp.name if cp is not None else f"Playlist {cid[:8]}"

    @staticmethod
    async def _start_top_op(
        emitter: ProgressEmitter | None,
        connector_name: str,
        total: int,
        *,
        parent_operation_id: str | None = None,
    ) -> str | None:
        # Web path: the SSE seam already owns a request operation. Use it as the
        # top op so per-playlist sub-ops are its DIRECT children — the subscriber
        # routes only one level, so a separate top op would orphan them. The seam
        # emits the `started` event and the aggregate progress flows to the same op.
        if parent_operation_id is not None:
            return parent_operation_id
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
        seam_owned: bool = False,
    ) -> None:
        # When the request op is the top op, the SSE seam owns its completion +
        # terminal event — completing it here would fail (it lives in the seam's
        # lifecycle) and pre-empt the seam's terminal event.
        if seam_owned or emitter is None or top_op_id is None:
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
        manager: ProgressBroker | None,
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
        manager: ProgressBroker | None,
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
    connector_playlist_identifiers: Sequence[ConnectorPlaylistIdentifier],
    sync_direction: SyncDirection = SyncDirection.PULL,
    *,
    force: bool = False,
    progress_emitter: ProgressEmitter | None = None,
    progress_broker: ProgressBroker | None = None,
    parent_operation_id: str | None = None,
    run_id: UUID | None = None,
) -> ImportConnectorPlaylistsAsCanonicalResult:
    """Convenience wrapper for route and CLI handlers.

    When called from the REST API, the route passes the bound emitter (for
    top-level op lifecycle), the ``ProgressBroker`` (for sub-op creation), and
    the ``run_id`` (so per-playlist failures land on the audit row). When called
    from the CLI or unit tests, all default to ``None`` and the use case emits
    zero events / records no issues — keeping the existing paths byte-identical.
    """
    from src.application.runner import execute_use_case
    from src.infrastructure.connectors._shared.metric_registry import (
        MetricConfigProviderImpl,
    )

    command = ImportConnectorPlaylistsAsCanonicalCommand(
        user_id=user_id,
        connector_name=connector_name,
        connector_playlist_identifiers=connector_playlist_identifiers,
        sync_direction=sync_direction,
        force=force,
    )
    use_case = ImportConnectorPlaylistsAsCanonicalUseCase(
        metric_config=MetricConfigProviderImpl()
    )
    return await execute_use_case(
        lambda uow: use_case.execute(
            command,
            uow,
            progress_emitter=progress_emitter,
            progress_broker=progress_broker,
            parent_operation_id=parent_operation_id,
            run_id=run_id,
        ),
        user_id=user_id,
    )
