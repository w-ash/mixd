"""Generic play import orchestration service implementing two-phase workflow.

Orchestrates the complete play import process with clean separation of concerns:
1. Phase 1: Raw data ingestion via pluggable importers (connector_plays)
2. Phase 2: Deferred resolution via ConnectorPlayResolutionService (track_plays)

This service contains generic business logic for the two-phase workflow while accepting
pluggable importer instances from the infrastructure layer.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Final

from attrs import define, field

from src.application.services.play_projection_service import (
    PROJECTION_STAT_LABELS,
    PlayProjectionService,
)
from src.application.use_cases._shared.batch_commit import commit_batch
from src.config import get_logger
from src.domain.entities import ConnectorTrackPlay, OperationResult, TrackPlay
from src.domain.entities.operations import (
    RESOLUTION_FAILURES_KEY,
    RESOLUTION_FAILURES_TRUNCATED_KEY,
)
from src.domain.entities.progress import (
    NullProgressEmitter,
    ProgressEmitter,
    create_progress_event,
    tracked_operation,
)
from src.domain.entities.shared import JsonValue
from src.domain.repositories.play import (
    PlayImporterProtocol,
    PlayImportParams,
    PlayResolverProtocol,
    ResolutionMetrics,
)
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)

# Resolvers wrap every per-item write in a savepoint, and PostgreSQL caches only
# 64 subtransaction ids per top-level transaction — they are never released
# before COMMIT, and past that cap every visibility check falls back to an
# on-disk SLRU lookup, so throughput collapses partway through a large import.
# Committing every 50 plays keeps worst-case savepoints-per-transaction under
# that cliff and bounds crash rework to a single chunk. Chunk size is a
# transaction-boundary policy, so it is owned here and never by a resolver.
_RESOLUTION_COMMIT_CHUNK_SIZE: Final = 50


# Bounds the JSONB ``issues`` array on the audit row: every recorded failure
# becomes one issue the Import History row renders, and an unbounded 198k-play
# export would otherwise put a 198k-element array in a single column. The full
# detail is in the server logs; the row carries the first N plus a count of the
# rest.
_MAX_RECORDED_RESOLUTION_FAILURES: Final = 50


def _describe_failure(failure: dict[str, str]) -> str:
    """One human-readable exemplar from a resolver's per-item failure entry."""
    track = failure.get("track", "unknown track")
    reason = failure.get("reason", "unknown reason")
    return f"{track} ({reason})"


@define(slots=True)
class _ResolutionFailureLog:
    """Bounded accumulation of per-item resolution failures.

    A resolver reports its failures per call, and the orchestrator calls it once
    per chunk per service — so the exemplar and the recorded list must be built
    across calls, not read off the last one. ``total`` keeps counting past the
    cap so the run can say how many were dropped.
    """

    recorded: list[dict[str, str]] = field(factory=list)
    total: int = 0

    def record(self, metrics: ResolutionMetrics) -> None:
        """Absorb one resolver call's failure list, respecting the cap."""
        failures = metrics.get("resolution_failures") or []
        self.total += len(failures)
        room = _MAX_RECORDED_RESOLUTION_FAILURES - len(self.recorded)
        if room > 0:
            self.recorded.extend(failures[:room])

    @property
    def truncated(self) -> int:
        """How many failures were seen but not recorded (0 when under the cap)."""
        return self.total - len(self.recorded)

    @property
    def exemplar(self) -> str | None:
        """The first recorded failure, rendered for the headline message."""
        return _describe_failure(self.recorded[0]) if self.recorded else None

    def write_to(self, metadata: dict[str, JsonValue]) -> None:
        """Publish the recorded failures onto an ``OperationResult``'s metadata.

        The SSE seam turns each entry into its own run issue, which is what makes
        the exact unresolved tracks visible in the Import History row.
        """
        if not self.recorded:
            return
        metadata[RESOLUTION_FAILURES_KEY] = [dict(f) for f in self.recorded]
        if self.truncated > 0:
            metadata[RESOLUTION_FAILURES_TRUNCATED_KEY] = self.truncated


def _partial_failure_message(
    errors: int, total: int, *, phase: str, exemplar: str | None
) -> str:
    """Build the top-level ``metadata["error"]`` for a run with per-item failures.

    ``OperationResult.is_failure`` flips on the ``errors`` summary metric alone,
    but ``failure_message`` reads only top-level ``metadata["error"]`` — without
    this the audit row and the log persist "errors: N" with no reason (the
    v0.10.2.2 gap). Per-phase detail stays in the nested metadata.
    """
    summary = f"{errors} of {total} plays failed {phase}"
    return f"{summary} (e.g. {exemplar})" if exemplar else summary


@define(slots=True)
class PlayImportOrchestrator:
    """Orchestrates two-phase play import workflow with clean architecture separation.

    Accepts pluggable importer instances to avoid mentioning specific connectors.
    All connector-specific logic lives in the infrastructure layer.
    """

    resolver_factory: Callable[[str], Awaitable[PlayResolverProtocol]]

    async def import_plays_two_phase(
        self,
        importer: PlayImporterProtocol,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        params: PlayImportParams,
        progress_emitter: ProgressEmitter | None = None,
    ) -> OperationResult:
        """Execute two-phase play import: ingestion then resolution.

        Args:
            importer: Pluggable importer instance from infrastructure layer
            uow: Unit of work for database operations
            user_id: The mixd user id, threaded to the importer for token-first
                account resolution (the cross-tenant leak fix)
            params: Importer-specific frozen import selectors
            progress_emitter: Optional progress emitter (defaults to null implementation)

        Returns:
            Combined operation result with ingestion and resolution metrics
        """
        if progress_emitter is None:
            progress_emitter = NullProgressEmitter()

        logger.info("Starting two-phase play import")

        # Phase 1: Raw data ingestion (connector_plays)
        logger.info("Phase 1: Ingesting raw play data")
        ingestion_result, connector_plays = await importer.import_plays(
            uow, params, user_id=user_id, progress_emitter=progress_emitter
        )

        if not connector_plays:
            logger.info("No plays to resolve - ingestion phase complete")
            return ingestion_result

        # Commit Phase 1 data so it survives if Phase 2 crashes
        await commit_batch(uow)

        # Phase 2: Deferred resolution (track_plays)
        logger.info(f"Phase 2: Resolving {len(connector_plays)} connector plays")
        resolution_result = await self.execute_resolution_phase(
            connector_plays, uow, user_id=user_id, progress_emitter=progress_emitter
        )

        # Combine results for unified reporting
        combined_result = self._combine_phase_results(
            ingestion_result, resolution_result
        )

        # Extract success rate from combined result summary metrics
        success_rate = combined_result.summary_metrics.get("success_rate")
        success_rate_str = f"{success_rate:.1f}%" if success_rate else "N/A"

        # Extract resolved plays from resolution result
        resolved_count = resolution_result.summary_metrics.get("resolved")

        logger.info(
            "Two-phase import complete",
            ingested_plays=len(connector_plays),
            resolved_plays=int(resolved_count),
            success_rate=success_rate_str,
        )

        return combined_result

    async def execute_resolution_phase(
        self,
        connector_plays: list[ConnectorTrackPlay],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        progress_emitter: ProgressEmitter,
    ) -> OperationResult:
        """Execute Phase 2: Resolve connector_plays to canonical track_plays.

        Uses existing clean resolvers directly - no redundant application service.
        """
        if not connector_plays:
            return self._create_empty_resolution_result()

        # Group connector plays by service for service-specific resolution
        spotify_plays = [p for p in connector_plays if p.connector_name == "spotify"]
        lastfm_plays = [p for p in connector_plays if p.connector_name == "lastfm"]

        all_track_plays: list[TrackPlay] = []
        # Only what Phase 3 still needs after a chunk is durable: the days the
        # projection has to sweep. The resolutions themselves are written back
        # per chunk, so holding them for the whole run would keep a 198k-play
        # export's worth of entities alive for nothing.
        resolved_played_at: list[datetime] = []
        failure_log = _ResolutionFailureLog()
        combined_metrics = {
            "total_plays": len(connector_plays),
            "resolved_plays": 0,
            "error_count": 0,
            "fallback_resolved": 0,
            "redirect_resolved": 0,
            "dead_ids_unresolved": 0,
            "isrc_suspect_deferred": 0,
        }

        async with tracked_operation(
            progress_emitter, "Resolving plays to canonical tracks"
        ) as operation_id:
            # Resolve plays per service using registry-provided resolvers
            for service, plays in [
                ("spotify", spotify_plays),
                ("lastfm", lastfm_plays),
            ]:
                if plays:
                    resolver = await self.resolver_factory(service)
                    for offset in range(0, len(plays), _RESOLUTION_COMMIT_CHUNK_SIZE):
                        chunk = plays[offset : offset + _RESOLUTION_COMMIT_CHUNK_SIZE]
                        outcome = await resolver.resolve_connector_plays(
                            chunk, uow, user_id=user_id
                        )
                        track_plays, metrics = outcome.track_plays, outcome.metrics
                        all_track_plays.extend(track_plays)
                        combined_metrics["resolved_plays"] += len(track_plays)
                        combined_metrics["error_count"] += metrics.get("error_count", 0)
                        combined_metrics["fallback_resolved"] += metrics.get(
                            "fallback_resolved", 0
                        )
                        combined_metrics["redirect_resolved"] += metrics.get(
                            "redirect_resolved", 0
                        )
                        combined_metrics["dead_ids_unresolved"] += metrics.get(
                            "dead_ids_unresolved", 0
                        )
                        combined_metrics["isrc_suspect_deferred"] += metrics.get(
                            "isrc_suspect_deferred", 0
                        )
                        failure_log.record(metrics)

                        # Resolution durability must match track durability: a
                        # killed run may lose at most one chunk of resolutions.
                        # The stamp goes into the SAME transaction that
                        # ``commit_batch`` below makes durable, alongside the
                        # connector_tracks, tracks and mappings this chunk
                        # produced. Accumulating the run's resolutions for one
                        # end-of-run write-back is what discarded all 15,317 of
                        # them when a 54-minute import was killed mid-run —
                        # every track was durable and every play still read as
                        # unresolved.
                        if outcome.resolutions:
                            ledger = uow.get_connector_play_repository()
                            _ = await ledger.bulk_update_resolution(
                                outcome.resolutions, resolved_at=datetime.now(UTC)
                            )
                            resolved_played_at.extend(
                                play.played_at for play, _ in outcome.resolutions
                            )

                        # Strictly between resolver calls: the resolver's
                        # per-item savepoints are all closed by the time it
                        # returns, and COMMIT inside a savepoint scope would
                        # discard the enclosing transaction state. Resolver
                        # writes are ON CONFLICT upserts, which is what makes
                        # a re-run over an already-committed chunk safe (the
                        # commit_batch contract).
                        await commit_batch(uow)

                        await progress_emitter.emit_progress(
                            create_progress_event(
                                operation_id=operation_id,
                                current=len(all_track_plays),
                                total=len(connector_plays),
                                message=f"Resolved {len(all_track_plays)}/{len(connector_plays)} plays ({service})",
                            )
                        )

            # Phase 3: project the affected window. Canonical plays are derived
            # from the observation ledger the chunk loop already stamped — the
            # resolver-built TrackPlays above only feed metrics. Projection
            # stays end-of-run rather than per chunk because it is day-scoped
            # and idempotent: re-running it after a crash costs one sweep of
            # the touched days and produces the same result.
            projection_stats: dict[str, int] = {}
            if resolved_played_at:
                projection = PlayProjectionService()
                # The projection gets its OWN operation, as the rebuild use
                # case does. The progress ledger enforces monotonic ``current``
                # per operation id, and the projection's chunk counter restarts
                # at 1 — emitted under the resolution operation (already at
                # ~15k after a large batch) every single event is rejected and
                # logged, freezing the bar for the whole projection tail.
                #
                # Re-entering the UoW context makes an exception mid-chunk roll
                # back the pending writes — ImportTracksUseCase converts
                # exceptions into a failed result, and without this bracket the
                # runner's session teardown would COMMIT the half-applied chunk
                # instead (per-chunk commit_batch durability is unaffected;
                # only the failure path changes).
                async with (
                    tracked_operation(
                        progress_emitter, "Projecting plays onto canonical history"
                    ) as projection_operation_id,
                    uow,
                ):
                    # Only the days these plays land in, not the span they
                    # straddle: one stray old play must not drag the batch
                    # through years of empty day-chunks.
                    projection_stats = await projection.project_observed_days(
                        uow,
                        user_id=user_id,
                        played_at=resolved_played_at,
                        progress_emitter=progress_emitter,
                        operation_id=projection_operation_id,
                    )
                logger.info(
                    "Projected batch window onto canonical plays",
                    **projection_stats,
                )

        # Convert to OperationResult with summary metrics
        result = OperationResult(
            operation_name="Connector Play Resolution",
            execution_time=0.0,  # Timing handled at orchestrator level
        )

        # Add metadata
        result.metadata.update(combined_metrics)
        if projection_stats:
            result.metadata["play_projection"] = projection_stats

        # Add summary metrics
        total_plays = combined_metrics["total_plays"]
        resolved = len(all_track_plays)
        errors = combined_metrics["error_count"]
        filtered = total_plays - resolved - errors

        result.summary_metrics.add("total", total_plays, "Total Plays", significance=0)
        result.summary_metrics.add(
            "resolved", resolved, "Track Plays Resolved", significance=1
        )
        for stat_key in (
            "groups_created",
            "groups_updated",
            "groups_merged",
            "resolution_divergence",
        ):
            if projection_stats.get(stat_key, 0) > 0:
                result.summary_metrics.add(
                    stat_key,
                    projection_stats[stat_key],
                    PROJECTION_STAT_LABELS[stat_key],
                    significance=2,
                )
        if filtered > 0:
            result.summary_metrics.add("filtered", filtered, "Filtered", significance=3)
        if errors > 0:
            exemplar = failure_log.exemplar
            result.summary_metrics.add("errors", errors, "Errors", significance=4)
            result.metadata["error"] = _partial_failure_message(
                errors, total_plays, phase="resolution", exemplar=exemplar
            )
            if exemplar is not None:
                result.metadata["first_failure"] = exemplar
            failure_log.write_to(result.metadata)

        fallback_resolved = combined_metrics["fallback_resolved"]
        redirect_resolved = combined_metrics["redirect_resolved"]
        dead_ids_unresolved = combined_metrics["dead_ids_unresolved"]
        isrc_suspect_deferred = combined_metrics["isrc_suspect_deferred"]

        if fallback_resolved > 0:
            result.summary_metrics.add(
                "fallback_resolved",
                fallback_resolved,
                "Resolved via Search Fallback",
                significance=5,
            )
        if redirect_resolved > 0:
            result.summary_metrics.add(
                "redirect_resolved",
                redirect_resolved,
                "Resolved via Spotify Redirect",
                significance=6,
            )
        if dead_ids_unresolved > 0:
            result.summary_metrics.add(
                "dead_ids_unresolved",
                dead_ids_unresolved,
                "Dead IDs Unresolved",
                significance=7,
            )
        if isrc_suspect_deferred > 0:
            result.summary_metrics.add(
                "isrc_suspect_deferred",
                isrc_suspect_deferred,
                "ISRC Suspect — Deferred to Review",
                significance=8,
            )

        return result

    def _create_empty_resolution_result(self) -> OperationResult:
        """Create empty resolution result when no plays to resolve."""
        result = OperationResult(
            operation_name="Connector Play Resolution",
            execution_time=0.0,
        )

        # Add metadata
        result.metadata.update({
            "total_plays": 0,
            "resolved_plays": 0,
            "error_count": 0,
        })

        # Add summary metrics showing zeros
        result.summary_metrics.add("total", 0, "Total Plays", significance=0)
        result.summary_metrics.add(
            "resolved", 0, "Track Plays Resolved", significance=1
        )

        return result

    def _combine_phase_results(
        self,
        ingestion_result: OperationResult,
        resolution_result: OperationResult,
    ) -> OperationResult:
        """Combine ingestion and resolution results into unified report.

        Provides user with comprehensive view of the two-phase workflow.
        """
        result = OperationResult(
            operation_name="Two-Phase Play Import",
            execution_time=ingestion_result.execution_time
            + resolution_result.execution_time,
        )

        # Combine metadata from both phases
        result.metadata["ingestion_phase"] = {
            "batch_id": ingestion_result.metadata.get("batch_id"),
            "checkpoint_timestamp": ingestion_result.metadata.get(
                "checkpoint_timestamp"
            ),
        }
        result.metadata["resolution_phase"] = resolution_result.metadata.copy()

        # Extract values from ingestion result summary metrics
        ingestion_imported = ingestion_result.summary_metrics.get("imported")
        ingestion_duplicates = ingestion_result.summary_metrics.get("duplicates")
        ingestion_errors = ingestion_result.summary_metrics.get("errors")
        raw_plays = ingestion_result.summary_metrics.get("raw_plays")

        # Extract values from resolution result summary metrics
        resolved = resolution_result.summary_metrics.get("resolved")
        resolution_filtered = resolution_result.summary_metrics.get("filtered")
        resolution_errors = resolution_result.summary_metrics.get("errors")
        fallback_resolved = resolution_result.summary_metrics.get("fallback_resolved")
        redirect_resolved = resolution_result.summary_metrics.get("redirect_resolved")
        dead_ids_unresolved = resolution_result.summary_metrics.get(
            "dead_ids_unresolved"
        )
        isrc_suspect_deferred = resolution_result.summary_metrics.get(
            "isrc_suspect_deferred"
        )

        total_errors = int(ingestion_errors + resolution_errors)

        # Add combined summary metrics
        result.summary_metrics.add(
            "raw_plays", int(raw_plays), "Raw Plays Found", significance=0
        )
        result.summary_metrics.add(
            "connector_plays",
            int(ingestion_imported),
            "Connector Plays Ingested",
            significance=1,
        )
        result.summary_metrics.add(
            "track_plays", int(resolved), "Track Plays Created", significance=2
        )

        if ingestion_duplicates > 0:
            result.summary_metrics.add(
                "duplicates",
                int(ingestion_duplicates),
                "Filtered (Duplicates)",
                significance=3,
            )
        if resolution_filtered > 0:
            result.summary_metrics.add(
                "filtered", int(resolution_filtered), "Filtered", significance=4
            )
        if total_errors > 0:
            result.summary_metrics.add("errors", total_errors, "Errors", significance=5)
            exemplar = resolution_result.metadata.get("first_failure")
            result.metadata["error"] = _partial_failure_message(
                total_errors,
                int(raw_plays),
                phase="import",
                exemplar=exemplar if isinstance(exemplar, str) else None,
            )
            # Re-surface the per-item failures at the top level: the SSE seam
            # reads the combined result, and ``resolution_phase`` is an opaque
            # nested blob to it.
            for key in (RESOLUTION_FAILURES_KEY, RESOLUTION_FAILURES_TRUNCATED_KEY):
                carried = resolution_result.metadata.get(key)
                if carried is not None:
                    result.metadata[key] = carried

        # Calculate success rate
        attempted = int(resolved + resolution_filtered + resolution_errors)
        if attempted > 0:
            success_rate = (resolved / attempted) * 100
            result.summary_metrics.add(
                "success_rate",
                success_rate,
                "Success Rate",
                format="percent",
                significance=6,
            )

        if fallback_resolved > 0:
            result.summary_metrics.add(
                "fallback_resolved",
                int(fallback_resolved),
                "Resolved via Search Fallback",
                significance=7,
            )
        if redirect_resolved > 0:
            result.summary_metrics.add(
                "redirect_resolved",
                int(redirect_resolved),
                "Resolved via Spotify Redirect",
                significance=8,
            )
        if dead_ids_unresolved > 0:
            result.summary_metrics.add(
                "dead_ids_unresolved",
                int(dead_ids_unresolved),
                "Dead IDs Unresolved",
                significance=9,
            )
        if isrc_suspect_deferred > 0:
            result.summary_metrics.add(
                "isrc_suspect_deferred",
                int(isrc_suspect_deferred),
                "ISRC Suspect — Deferred to Review",
                significance=10,
            )

        return result
