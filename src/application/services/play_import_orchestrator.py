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
from src.config.logging import logging_context
from src.config.telemetry import ChunkProbe, measure_chunk, phase
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

# What actually bounds this number, measured (v0.10.2.11):
#   - Crash rework: a killed run loses at most one chunk.
#   - PostgreSQL's 64-subxid cache. On the happy path a chunk opens ~10-15
#     savepoints regardless of size — ``persist_bulk_with_item_fallback``
#     savepoints the *batch*, not the item — so this is not the binding
#     constraint it was once documented to be. It binds only on the degraded
#     path, where a poisoned bulk write retries per item; a subxid is released
#     on rollback but *retained* through commit, so the count there tracks
#     successes. That path signals ``degraded_persists`` and commits early
#     instead of being priced in here.
#   - Bind-parameter ceilings on the multi-VALUES bulk writes downstream.
# Chunk size is a transaction-boundary policy, so it is owned here, never by
# a resolver.
_RESOLUTION_COMMIT_CHUNK_SIZE: Final = 50


# Bounds the JSONB ``issues`` array on the audit row: every recorded failure
# becomes one issue the Import History row renders, and an unbounded 198k-play
# export would otherwise put a 198k-element array in a single column. The full
# detail is in the server logs; the row carries the first N plus a count of the
# rest.
_MAX_RECORDED_RESOLUTION_FAILURES: Final = 50


# Resolver counts carried on the flight line so a chunk's timings can be read
# against its shape — a creation-heavy chunk and a reuse-heavy one of the same
# size cost very different amounts.
_CHUNK_METRIC_KEYS: Final = (
    "resolved_tracks",
    "new_tracks",
    "reused_tracks",
    "error_count",
    "duration_excluded",
    "incognito_excluded",
    "suppressed",
    "degraded_persists",
    "redirect_resolved",
    "fallback_resolved",
    "isrc_suspect_deferred",
    "write_failed",
)

# The resolver counts a run's metadata carries, summed over its chunks. Every
# per-chunk counter belongs here too: one that the flight line reports and the
# run record drops is readable only while the machine's logs survive, and a
# run-level zero for a key that is merely absent reads as evidence of health.
_RUN_METRIC_KEYS: Final = (
    "error_count",
    "duration_excluded",
    "incognito_excluded",
    "fallback_resolved",
    "redirect_resolved",
    "dead_ids_unresolved",
    "isrc_suspect_deferred",
    "reused_tracks",
    "suppressed",
    "degraded_persists",
    "write_failed",
)

# The subset of the above the run record labels, in display order. ``write_failed``
# sits beside ``dead_ids_unresolved`` because it is the half of it that means
# "ask again", and reading one without the other is what turned a chunk of
# refused writes into a diagnosis of dead identifiers.
_RESOLUTION_SUMMARY_METRICS: Final[tuple[tuple[str, str, int], ...]] = (
    # The breakdown behind "Filtered", sharing its significance so the two sort
    # together. A service export is an event log, so discarding a third of it is
    # normal — but that is only reassuring if the user can see the discards are
    # skips and private sessions rather than tracks mixd failed to identify.
    ("duration_excluded", "Skipped — Too Short to Count", 3),
    ("incognito_excluded", "Skipped — Private Session", 3),
    ("fallback_resolved", "Resolved via Search Fallback", 5),
    ("redirect_resolved", "Resolved via Spotify Redirect", 6),
    ("dead_ids_unresolved", "Dead IDs Unresolved", 7),
    ("write_failed", "Writes Rolled Back — Retry Next Import", 8),
    ("isrc_suspect_deferred", "ISRC Suspect — Deferred to Review", 9),
    ("suppressed", "Skipped — Inside Backoff Window", 10),
    ("reused_tracks", "Reused Existing Canonicals", 11),
    ("degraded_persists", "Chunks Written One Row at a Time", 12),
)

# Resolution-phase counters the terminal combined result re-publishes, in its
# own display order. The combined result is what ``operation_runs.counts`` is
# built from, so a counter missing here never reaches the user's run record no
# matter how faithfully the resolution phase counted it.
_CARRIED_RESOLUTION_METRICS: Final[tuple[tuple[str, str, int], ...]] = (
    ("duration_excluded", "Skipped — Too Short to Count", 4),
    ("incognito_excluded", "Skipped — Private Session", 4),
    ("fallback_resolved", "Resolved via Search Fallback", 7),
    ("redirect_resolved", "Resolved via Spotify Redirect", 8),
    ("dead_ids_unresolved", "Dead IDs Unresolved", 9),
    ("isrc_suspect_deferred", "ISRC Suspect — Deferred to Review", 10),
)


def _log_chunk_flight(
    probe: ChunkProbe,
    *,
    service: str,
    chunk_index: int,
    plays: int,
    metrics: ResolutionMetrics,
) -> None:
    """Emit the import's per-chunk flight recording.

    This line is the instrument, not scaffolding: ``db_stmts`` and ``rtt_ms``
    are what turn "slower than designed" into arithmetic, and ``db_ops`` names
    the owner when they disagree.
    """
    logger.info(
        "play_import_chunk",
        service=service,
        chunk=chunk_index,
        plays=plays,
        wall_ms=round(probe.wall_ms),
        db_ms=round(probe.db_ms),
        db_stmts=probe.statements,
        off_db_ms=round(probe.wall_ms - probe.db_ms),
        rtt_ms=round(probe.rtt_ms, 1),
        api_calls=probe.api_calls,
        api_ms_sum=round(probe.api_ms_sum),
        api_ms=round(probe.phase_ms("api")),
        resolve_ms=round(probe.phase_ms("resolve")),
        writeback_ms=round(probe.phase_ms("writeback")),
        commit_ms=round(probe.phase_ms("commit")),
        db_ops=probe.top_operations(),
        **{key: metrics.get(key, 0) for key in _CHUNK_METRIC_KEYS},
    )


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
        } | dict.fromkeys(_RUN_METRIC_KEYS, 0)

        async with tracked_operation(
            progress_emitter, "Resolving plays to canonical tracks", phase="match"
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
                        chunk_index = offset // _RESOLUTION_COMMIT_CHUNK_SIZE
                        with logging_context(
                            import_service=service, import_chunk=chunk_index
                        ):
                            async with measure_chunk() as probe:
                                async with phase("resolve"):
                                    outcome = await resolver.resolve_connector_plays(
                                        chunk, uow, user_id=user_id
                                    )
                                track_plays = outcome.track_plays
                                metrics = outcome.metrics

                                # Resolution durability must match track
                                # durability: a killed run may lose at most one
                                # chunk of resolutions. The stamp goes into the
                                # SAME transaction that ``commit_batch`` below
                                # makes durable, alongside the connector_tracks,
                                # tracks and mappings this chunk produced.
                                # Accumulating the run's resolutions for one
                                # end-of-run write-back is what discarded all
                                # 15,317 of them when a 54-minute import was
                                # killed mid-run — every track was durable and
                                # every play still read as unresolved.
                                # Exclusions ride the same transaction for the
                                # same reason: a row that reads as excluded but
                                # carries no reason is indistinguishable from
                                # one that failed to resolve, which is exactly
                                # the ambiguity the reason column removes.
                                if outcome.resolutions or outcome.exclusions:
                                    ledger = uow.get_connector_play_repository()
                                    async with phase("writeback"):
                                        _ = await ledger.bulk_update_resolution(
                                            outcome.resolutions,
                                            resolved_at=datetime.now(UTC),
                                        )
                                        _ = await ledger.bulk_update_exclusions(
                                            outcome.exclusions
                                        )
                                    resolved_played_at.extend(
                                        play.played_at
                                        for play, _ in outcome.resolutions
                                    )

                                # Strictly between resolver calls: the resolver's
                                # savepoints are all closed by the time it
                                # returns, and COMMIT inside a savepoint scope
                                # would discard the enclosing transaction state.
                                # Resolver writes are ON CONFLICT upserts, which
                                # is what makes a re-run over an already-committed
                                # chunk safe (the commit_batch contract).
                                async with phase("commit"):
                                    await commit_batch(uow)

                            _log_chunk_flight(
                                probe,
                                service=service,
                                chunk_index=chunk_index,
                                plays=len(chunk),
                                metrics=metrics,
                            )

                        all_track_plays.extend(track_plays)
                        combined_metrics["resolved_plays"] += len(track_plays)
                        for key in _RUN_METRIC_KEYS:
                            combined_metrics[key] += metrics.get(key, 0)
                        failure_log.record(metrics)

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
                        progress_emitter,
                        "Projecting plays onto canonical history",
                        phase="save",
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

        # Only ``to_counts()`` — summary metrics — reaches the run record, so a
        # counter that stops here is readable only in the machine's logs. Zero
        # is left off rather than recorded: a clean run says nothing, and the
        # absence of a row is not evidence about a run that never had the key.
        for key, label, significance in _RESOLUTION_SUMMARY_METRICS:
            value = combined_metrics[key]
            if value > 0:
                result.summary_metrics.add(key, value, label, significance=significance)

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

        # Table-driven rather than a block per metric: the resolution phase and
        # the combined result each used to name their counters independently,
        # so a counter added to one was silently absent from the other — which
        # is how the skip breakdown reached the logs but never the run record.
        for key, label, significance in _CARRIED_RESOLUTION_METRICS:
            value = resolution_result.summary_metrics.get(key)
            if value > 0:
                result.summary_metrics.add(
                    key, int(value), label, significance=significance
                )

        return result
