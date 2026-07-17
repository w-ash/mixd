"""Rebuild canonical play history from the observation ledger.

Canonical plays are a deterministic projection of ``connector_plays`` —
this use case replays that projection over the full ledger span, so any
past merge defect (or future algorithm change) is repaired by re-derivation
instead of bespoke surgery. A final reconciliation pass removes canonical
plays no observation backs: after a rebuild, ``track_plays`` IS the
projection, nothing more.

``dry_run`` reports the exact diff (create/update/merge/delete counts and
divergences) without writing anything.
"""

from uuid import UUID

from attrs import define, field

from src.application.services.play_projection_service import (
    PROJECTION_STAT_LABELS,
    PlayProjectionService,
)
from src.application.use_cases._shared.batch_commit import commit_batch
from src.config import get_logger
from src.domain.entities import OperationResult
from src.domain.entities.progress import (
    NullProgressEmitter,
    ProgressEmitter,
    tracked_operation,
)
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)

# Rendering order for the rebuild summary; labels come from the shared
# projection table so import and rebuild describe the same event identically.
_SUMMARY_LABELS: tuple[tuple[str, str], ...] = (
    ("groups_created", PROJECTION_STAT_LABELS["groups_created"]),
    ("groups_updated", PROJECTION_STAT_LABELS["groups_updated"]),
    ("groups_merged", PROJECTION_STAT_LABELS["groups_merged"]),
    ("groups_unchanged", PROJECTION_STAT_LABELS["groups_unchanged"]),
    ("orphaned_deleted", PROJECTION_STAT_LABELS["orphaned_deleted"]),
    ("unsourced_deleted", "Unsourced Deleted"),
    ("resolution_divergence", PROJECTION_STAT_LABELS["resolution_divergence"]),
    ("same_channel_collapsed", PROJECTION_STAT_LABELS["same_channel_collapsed"]),
)


@define(frozen=True, slots=True)
class RebuildPlayHistoryCommand:
    """Selectors for a full-history play projection rebuild."""

    user_id: str
    dry_run: bool = False


@define(frozen=True, slots=True)
class RebuildPlayHistoryResult:
    """Rebuild outcome: per-arm diff counts plus a renderable result."""

    result: OperationResult
    stats: dict[str, int] = field(factory=dict)
    dry_run: bool = False


@define(slots=True)
class RebuildPlayHistoryUseCase:
    """Replay the ledger projection over the full history."""

    async def execute(
        self,
        command: RebuildPlayHistoryCommand,
        uow: UnitOfWorkProtocol,
        progress_emitter: ProgressEmitter | None = None,
    ) -> RebuildPlayHistoryResult:
        if progress_emitter is None:
            progress_emitter = NullProgressEmitter()

        service = PlayProjectionService()
        operation_name = (
            "Play History Rebuild (dry run)"
            if command.dry_run
            else "Play History Rebuild"
        )

        async with (
            uow,
            tracked_operation(
                progress_emitter, "Rebuilding play history from the ledger"
            ) as operation_id,
        ):
            span = await service.full_range(uow, user_id=command.user_id)
            if span is None:
                logger.info(
                    "No resolved ledger rows — nothing to rebuild",
                    user_id=command.user_id,
                )
                return self._build_result({}, operation_name, command.dry_run)

            start, end = span
            # Dry run: collect the play ids the projection would claim so
            # reconciliation can be simulated against the would-be state
            # instead of the current (membership-less) one.
            claimed: set[UUID] | None = set() if command.dry_run else None
            stats = await service.project_range(
                uow,
                user_id=command.user_id,
                start=start,
                end=end,
                dry_run=command.dry_run,
                progress_emitter=progress_emitter,
                operation_id=operation_id,
                claimed_play_ids=claimed,
            )

            # Reconciliation: canonical rows no observation backs are not part
            # of the projection. Dry run reports them; a real run removes them.
            plays_repo = uow.get_plays_repository()
            unsourced = await plays_repo.find_unsourced_play_ids(
                user_id=command.user_id
            )
            if command.dry_run:
                stats["unsourced_deleted"] = len(set(unsourced) - (claimed or set()))
            elif unsourced:
                stats[
                    "unsourced_deleted"
                ] = await plays_repo.delete_plays_without_sources(
                    unsourced, user_id=command.user_id
                )
                await commit_batch(uow)
            else:
                stats["unsourced_deleted"] = 0

        logger.info(
            "Play history rebuild complete",
            user_id=command.user_id,
            dry_run=command.dry_run,
            **stats,
        )
        return self._build_result(stats, operation_name, command.dry_run)

    def _build_result(
        self, stats: dict[str, int], operation_name: str, dry_run: bool
    ) -> RebuildPlayHistoryResult:
        result = OperationResult(operation_name=operation_name, execution_time=0.0)
        result.metadata["dry_run"] = dry_run
        result.metadata["play_projection"] = dict(stats)
        for significance, (key, label) in enumerate(_SUMMARY_LABELS):
            value = stats.get(key, 0)
            if value > 0 or key in ("groups_created", "groups_unchanged"):
                result.summary_metrics.add(key, value, label, significance=significance)
        return RebuildPlayHistoryResult(
            result=result, stats=dict(stats), dry_run=dry_run
        )


async def run_rebuild(
    *,
    user_id: str,
    dry_run: bool = False,
    progress_emitter: ProgressEmitter | None = None,
) -> RebuildPlayHistoryResult:
    """Convenience wrapper mirroring ``run_import`` for CLI and launchers."""
    from src.application.runner import execute_use_case

    command = RebuildPlayHistoryCommand(user_id=user_id, dry_run=dry_run)
    return await execute_use_case(
        lambda uow: RebuildPlayHistoryUseCase().execute(
            command, uow, progress_emitter=progress_emitter
        ),
        user_id=user_id,
    )
