"""Unit tests for PlayImportOrchestrator two-phase workflow.

Tests the orchestration layer: phase coordination, result combination,
short-circuit on empty ingestion, and error handling.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from structlog.testing import capture_logs

from src.application.services.play_import_orchestrator import (
    _MAX_RECORDED_RESOLUTION_FAILURES,
    PlayImportOrchestrator,
)
from src.application.services.progress_broker import ProgressBroker
from src.domain.entities import ConnectorTrackPlay, OperationResult, TrackPlay
from src.domain.entities.progress import (
    NullProgressEmitter,
    OperationStatus,
    ProgressEvent,
    ProgressOperation,
)
from src.domain.repositories.play import (
    LastfmImportParams,
    PlayResolutionOutcome,
    SpotifyImportParams,
)
from tests.fixtures.mocks import make_mock_uow

_RESOLVED_TRACK_ID = UUID("00000000-0000-7000-8000-00000000000a")


def _make_ingestion_result(
    imported: int = 10,
    raw_plays: int = 15,
    duplicates: int = 5,
) -> OperationResult:
    """Create a mock ingestion phase OperationResult."""
    result = OperationResult(
        operation_name="Spotify Connector Play Import",
        execution_time=1.5,
    )
    result.metadata["batch_id"] = "test-batch-123"
    result.summary_metrics.add(
        "raw_plays", raw_plays, "Raw Plays Found", significance=0
    )
    result.summary_metrics.add(
        "imported", imported, "Track Plays Created", significance=1
    )
    if duplicates > 0:
        result.summary_metrics.add(
            "duplicates", duplicates, "Filtered (Duplicates)", significance=3
        )
    return result


def _make_resolved_track_play(track_id: int = 1) -> TrackPlay:
    """Create a real TrackPlay for testing resolution output."""
    return TrackPlay(
        track_id=track_id,
        service="spotify",
        played_at=datetime(2024, 6, 15, 14, 30, tzinfo=UTC),
        ms_played=240000,
        import_source="spotify_export",
    )


def _make_connector_play(
    track_name: str = "Test Song",
    played_at: datetime = datetime(2024, 6, 15, 14, 30, tzinfo=UTC),
) -> ConnectorTrackPlay:
    """Create a ConnectorTrackPlay for testing."""
    return ConnectorTrackPlay(
        service="spotify",
        track_name=track_name,
        artist_name="Test Artist",
        album_name="Test Album",
        played_at=played_at,
        ms_played=240000,
        service_metadata={"track_uri": "spotify:track:abc123def456ghi789jk"},
        import_timestamp=datetime(2024, 7, 1, tzinfo=UTC),
        import_source="spotify_export",
        import_batch_id="test-batch",
    )


@pytest.fixture
def mock_resolver():
    """Default mock resolver returning 0 resolved plays."""
    resolver = AsyncMock()
    resolver.resolve_connector_plays.return_value = PlayResolutionOutcome(
        track_plays=[], metrics={"error_count": 0}, resolutions=()
    )
    return resolver


@pytest.fixture
def orchestrator(mock_resolver):
    async def resolver_factory(service: str):
        return mock_resolver

    return PlayImportOrchestrator(resolver_factory=resolver_factory)


@pytest.fixture
def mock_uow():
    uow = make_mock_uow()
    # Use .return_value attribute access (not a call) to avoid poisoning call count —
    # TestEmptyIngestion.test_no_plays_short_circuits asserts get_plays_repository.assert_not_called()
    plays_repo = uow.get_plays_repository.return_value
    plays_repo.bulk_insert_plays.return_value = (5, 0)
    return uow


@pytest.fixture
def mock_importer():
    return AsyncMock()


class TestTwoPhaseHappyPath:
    """Test the normal two-phase workflow."""

    async def test_ingestion_then_resolution(
        self, orchestrator, mock_resolver, mock_uow, mock_importer
    ):
        """Happy path: ingestion returns plays → resolution resolves them."""
        connector_plays = [_make_connector_play(f"Song {i}") for i in range(3)]
        mock_importer.import_plays.return_value = (
            _make_ingestion_result(imported=3, raw_plays=3, duplicates=0),
            connector_plays,
        )

        # Configure the injected resolver for this test
        mock_resolver.resolve_connector_plays.return_value = PlayResolutionOutcome(
            track_plays=[_make_resolved_track_play(track_id=i + 1) for i in range(3)],
            metrics={"error_count": 0},
            resolutions=(),
        )

        result = await orchestrator.import_plays_two_phase(
            mock_importer,
            mock_uow,
            user_id="test-user",
            params=SpotifyImportParams(file_path=Path("/fake/path.json")),
        )

        assert result.operation_name == "Two-Phase Play Import"
        # Ingestion should have been called
        mock_importer.import_plays.assert_called_once()

    async def test_combined_result_has_both_phase_metadata(
        self, orchestrator, mock_resolver, mock_uow, mock_importer
    ):
        connector_plays = [_make_connector_play()]
        mock_importer.import_plays.return_value = (
            _make_ingestion_result(imported=1, raw_plays=1, duplicates=0),
            connector_plays,
        )

        mock_resolver.resolve_connector_plays.return_value = PlayResolutionOutcome(
            track_plays=[_make_resolved_track_play()],
            metrics={"error_count": 0},
            resolutions=(),
        )

        result = await orchestrator.import_plays_two_phase(
            mock_importer, mock_uow, user_id="test-user", params=LastfmImportParams()
        )

        assert "ingestion_phase" in result.metadata
        assert "resolution_phase" in result.metadata
        assert result.metadata["ingestion_phase"]["batch_id"] == "test-batch-123"


class TestEmptyIngestion:
    """Test short-circuit when ingestion produces no plays."""

    async def test_no_plays_short_circuits(self, orchestrator, mock_uow, mock_importer):
        """Empty ingestion should return early without resolution phase."""
        ingestion_result = _make_ingestion_result(imported=0, raw_plays=0, duplicates=0)
        mock_importer.import_plays.return_value = (ingestion_result, [])

        result = await orchestrator.import_plays_two_phase(
            mock_importer, mock_uow, user_id="test-user", params=LastfmImportParams()
        )

        # Should return the ingestion result directly
        assert result is ingestion_result
        # Resolution phase should not be attempted
        mock_uow.get_plays_repository.assert_not_called()


class TestResolutionPhaseErrors:
    """Test error handling in the resolution phase."""

    async def test_resolution_errors_captured_in_metrics(
        self, orchestrator, mock_resolver, mock_uow, mock_importer
    ):
        connector_plays = [_make_connector_play()]
        mock_importer.import_plays.return_value = (
            _make_ingestion_result(imported=1, raw_plays=1, duplicates=0),
            connector_plays,
        )

        mock_resolver.resolve_connector_plays.return_value = PlayResolutionOutcome(
            track_plays=[],  # No resolved plays
            metrics={"error_count": 1},  # 1 error
            resolutions=(),
        )

        result = await orchestrator.import_plays_two_phase(
            mock_importer, mock_uow, user_id="test-user", params=LastfmImportParams()
        )

        # The resolution phase error should be in combined metrics
        assert result.metadata["resolution_phase"]["error_count"] == 1

    async def test_partial_failure_carries_top_level_error_message(
        self, orchestrator, mock_resolver, mock_uow, mock_importer
    ):
        """is_failure flips on the errors metric, but failure_message reads only
        top-level metadata["error"] — a partial failure used to record
        "errors: 1" with no reason anywhere queryable."""
        connector_plays = [_make_connector_play()]
        mock_importer.import_plays.return_value = (
            _make_ingestion_result(imported=1, raw_plays=1, duplicates=0),
            connector_plays,
        )
        mock_resolver.resolve_connector_plays.return_value = PlayResolutionOutcome(
            track_plays=[],
            metrics={
                "error_count": 1,
                "resolution_failures": [
                    {
                        "track": "Test Artist - Test Song",
                        "reason": "track_resolution_failed",
                    }
                ],
            },
            resolutions=(),
        )

        result = await orchestrator.import_plays_two_phase(
            mock_importer, mock_uow, user_id="test-user", params=LastfmImportParams()
        )

        assert result.is_failure
        message = result.failure_message
        assert message is not None
        assert "1 of 1 plays failed" in message
        assert "Test Artist - Test Song" in message
        # Nested per-phase detail stays where it was
        assert result.metadata["resolution_phase"]["error_count"] == 1

    async def test_clean_run_records_no_error_message(
        self, orchestrator, mock_resolver, mock_uow, mock_importer
    ):
        connector_plays = [_make_connector_play()]
        mock_importer.import_plays.return_value = (
            _make_ingestion_result(imported=1, raw_plays=1, duplicates=0),
            connector_plays,
        )
        mock_resolver.resolve_connector_plays.return_value = PlayResolutionOutcome(
            track_plays=[_make_resolved_track_play()],
            metrics={"error_count": 0},
            resolutions=(),
        )

        result = await orchestrator.import_plays_two_phase(
            mock_importer, mock_uow, user_id="test-user", params=LastfmImportParams()
        )

        assert not result.is_failure
        assert result.failure_message is None


class TestCombinePhaseResults:
    """Test _combine_phase_results() metric aggregation."""

    def test_combined_metrics_calculated(self, orchestrator):
        ingestion = _make_ingestion_result(imported=10, raw_plays=15, duplicates=5)

        resolution = OperationResult(
            operation_name="Connector Play Resolution",
            execution_time=2.0,
        )
        resolution.metadata.update({
            "total_plays": 10,
            "resolved_plays": 8,
            "error_count": 2,
        })
        resolution.summary_metrics.add("total", 10, "Total Plays", significance=0)
        resolution.summary_metrics.add(
            "resolved", 8, "Track Plays Resolved", significance=1
        )
        resolution.summary_metrics.add("errors", 2, "Errors", significance=3)

        result = orchestrator._combine_phase_results(ingestion, resolution)

        # Check combined execution time
        assert result.execution_time == 3.5  # 1.5 + 2.0

        # Check combined summary metrics by name
        metric_map = {m.name: m.value for m in result.summary_metrics.metrics}
        assert metric_map["raw_plays"] == 15
        assert metric_map["connector_plays"] == 10
        assert metric_map["track_plays"] == 8
        assert metric_map["duplicates"] == 5
        assert metric_map["errors"] == 2

    def test_success_rate_with_zero_denominator(self, orchestrator):
        """Zero attempted plays should not cause division error."""
        ingestion = _make_ingestion_result(imported=0, raw_plays=0, duplicates=0)

        resolution = OperationResult(operation_name="Resolution", execution_time=0.0)
        resolution.metadata.update({
            "total_plays": 0,
            "resolved_plays": 0,
            "error_count": 0,
        })
        resolution.summary_metrics.add("total", 0, "Total", significance=0)
        resolution.summary_metrics.add("resolved", 0, "Resolved", significance=1)

        # Should not raise
        result = orchestrator._combine_phase_results(ingestion, resolution)

        # success_rate should not be present when no plays attempted
        metric_names = {m.name for m in result.summary_metrics.metrics}
        assert "success_rate" not in metric_names

    def test_success_rate_calculated_when_plays_attempted(self, orchestrator):
        ingestion = _make_ingestion_result(imported=10, raw_plays=10, duplicates=0)

        resolution = OperationResult(operation_name="Resolution", execution_time=0.0)
        resolution.metadata.update({
            "total_plays": 10,
            "resolved_plays": 8,
            "error_count": 0,
        })
        resolution.summary_metrics.add("total", 10, "Total", significance=0)
        resolution.summary_metrics.add("resolved", 8, "Resolved", significance=1)
        resolution.summary_metrics.add("filtered", 2, "Filtered", significance=2)

        result = orchestrator._combine_phase_results(ingestion, resolution)

        metric_map = {m.name: m.value for m in result.summary_metrics.metrics}
        assert "success_rate" in metric_map
        assert metric_map["success_rate"] == pytest.approx(80.0)


class TestSpotifyResolutionMetricsPropagation:
    """fallback_resolved/redirect_resolved/dead_ids_unresolved/isrc_suspect_deferred
    from the per-connector ResolutionMetrics dict must reach summary_metrics —
    previously only error_count was extracted from the resolver's metrics dict,
    so these counts were computed but silently dropped."""

    async def test_resolution_phase_summary_metrics_carry_spotify_counts(
        self, orchestrator, mock_resolver, mock_uow
    ):
        connector_plays = [_make_connector_play()]
        mock_resolver.resolve_connector_plays.return_value = PlayResolutionOutcome(
            track_plays=[_make_resolved_track_play()],
            metrics={
                "error_count": 0,
                "fallback_resolved": 2,
                "redirect_resolved": 3,
                "dead_ids_unresolved": 1,
                "isrc_suspect_deferred": 4,
            },
            resolutions=(),
        )

        result = await orchestrator.execute_resolution_phase(
            connector_plays,
            mock_uow,
            user_id="test-user",
            progress_emitter=NullProgressEmitter(),
        )

        counts = result.to_counts()
        assert counts["fallback_resolved"] == 2
        assert counts["redirect_resolved"] == 3
        assert counts["dead_ids_unresolved"] == 1
        assert counts["isrc_suspect_deferred"] == 4

    async def test_run_record_carries_every_per_chunk_counter(
        self, orchestrator, mock_resolver, mock_uow
    ):
        """A counter the chunk log reports and the run record drops is readable
        only while the machine's logs live — and reads as zero once they don't."""
        connector_plays = [_make_connector_play()]
        mock_resolver.resolve_connector_plays.return_value = PlayResolutionOutcome(
            track_plays=[_make_resolved_track_play()],
            metrics={
                "error_count": 0,
                "suppressed": 5,
                "reused_tracks": 6,
                "degraded_persists": 7,
                "write_failed": 8,
            },
            resolutions=(),
        )

        result = await orchestrator.execute_resolution_phase(
            connector_plays,
            mock_uow,
            user_id="test-user",
            progress_emitter=NullProgressEmitter(),
        )

        counts = result.to_counts()
        assert counts["suppressed"] == 5
        assert counts["reused_tracks"] == 6
        assert counts["degraded_persists"] == 7
        assert counts["write_failed"] == 8

    async def test_two_phase_import_final_result_carries_spotify_counts(
        self, orchestrator, mock_resolver, mock_uow, mock_importer
    ):
        """The counts must survive all the way to the terminal combined
        OperationResult — the one operation_runs.counts is built from."""
        connector_plays = [_make_connector_play()]
        mock_importer.import_plays.return_value = (
            _make_ingestion_result(imported=1, raw_plays=1, duplicates=0),
            connector_plays,
        )
        mock_resolver.resolve_connector_plays.return_value = PlayResolutionOutcome(
            track_plays=[_make_resolved_track_play()],
            metrics={
                "error_count": 0,
                "fallback_resolved": 2,
                "redirect_resolved": 3,
                "dead_ids_unresolved": 1,
                "isrc_suspect_deferred": 4,
            },
            resolutions=(),
        )

        result = await orchestrator.import_plays_two_phase(
            mock_importer,
            mock_uow,
            user_id="test-user",
            params=SpotifyImportParams(file_path=Path("/fake/path.json")),
        )

        assert result.operation_name == "Two-Phase Play Import"
        counts = result.to_counts()
        assert counts["fallback_resolved"] == 2
        assert counts["redirect_resolved"] == 3
        assert counts["dead_ids_unresolved"] == 1
        assert counts["isrc_suspect_deferred"] == 4

    def test_zero_counts_omitted_from_summary_metrics(self, orchestrator):
        """Counters at zero should not clutter summary_metrics (matches the
        existing filtered/errors conditional-add convention)."""
        ingestion = _make_ingestion_result(imported=10, raw_plays=10, duplicates=0)

        resolution = OperationResult(operation_name="Resolution", execution_time=0.0)
        resolution.summary_metrics.add("total", 10, "Total", significance=0)
        resolution.summary_metrics.add("resolved", 10, "Resolved", significance=1)

        result = orchestrator._combine_phase_results(ingestion, resolution)

        metric_names = {m.name for m in result.summary_metrics.metrics}
        assert "fallback_resolved" not in metric_names
        assert "redirect_resolved" not in metric_names
        assert "dead_ids_unresolved" not in metric_names
        assert "isrc_suspect_deferred" not in metric_names


class TestIncrementalCommit:
    """Verify commit_batch() is called between Phase 1 and Phase 2."""

    async def test_commit_batch_after_phase1(
        self, orchestrator, mock_resolver, mock_uow, mock_importer
    ):
        """Phase 1 data should be committed before Phase 2 starts."""
        connector_plays = [_make_connector_play()]
        mock_importer.import_plays.return_value = (
            _make_ingestion_result(imported=1, raw_plays=1, duplicates=0),
            connector_plays,
        )
        mock_resolver.resolve_connector_plays.return_value = PlayResolutionOutcome(
            track_plays=[_make_resolved_track_play()],
            metrics={"error_count": 0},
            resolutions=(),
        )

        await orchestrator.import_plays_two_phase(
            mock_importer, mock_uow, user_id="test-user", params=LastfmImportParams()
        )

        # Two boundaries for a one-play import: the Phase 1 ingestion commit,
        # then the single resolution chunk.
        assert mock_uow.commit_batch.await_count == 2

    async def test_no_commit_batch_on_empty_ingestion(
        self, orchestrator, mock_uow, mock_importer
    ):
        """Empty ingestion short-circuits before commit_batch."""
        mock_importer.import_plays.return_value = (
            _make_ingestion_result(imported=0, raw_plays=0, duplicates=0),
            [],
        )

        await orchestrator.import_plays_two_phase(
            mock_importer, mock_uow, user_id="test-user", params=LastfmImportParams()
        )

        mock_uow.commit_batch.assert_not_awaited()


class TestPhase2Progress:
    """Verify progress emission during Phase 2 resolution."""

    async def test_phase2_emits_progress(
        self, orchestrator, mock_resolver, mock_uow, mock_importer
    ):
        """Resolution phase should emit progress events per service."""
        connector_plays = [_make_connector_play(f"Song {i}") for i in range(3)]
        mock_importer.import_plays.return_value = (
            _make_ingestion_result(imported=3, raw_plays=3, duplicates=0),
            connector_plays,
        )
        mock_resolver.resolve_connector_plays.return_value = PlayResolutionOutcome(
            track_plays=[_make_resolved_track_play(track_id=i + 1) for i in range(3)],
            metrics={"error_count": 0},
            resolutions=(),
        )

        emitter = AsyncMock()
        emitter.start_operation = AsyncMock(return_value="phase2-op-id")
        emitter.emit_progress = AsyncMock()
        emitter.complete_operation = AsyncMock()

        await orchestrator.import_plays_two_phase(
            mock_importer,
            mock_uow,
            user_id="test-user",
            params=LastfmImportParams(),
            progress_emitter=emitter,
        )

        emitter.start_operation.assert_awaited()
        emitter.emit_progress.assert_awaited()
        emitter.complete_operation.assert_awaited()

    async def test_no_phase2_progress_on_empty(
        self, orchestrator, mock_uow, mock_importer
    ):
        """Empty ingestion should not start a Phase 2 progress operation."""
        mock_importer.import_plays.return_value = (
            _make_ingestion_result(imported=0, raw_plays=0, duplicates=0),
            [],
        )

        emitter = AsyncMock()
        emitter.start_operation = AsyncMock(return_value="phase2-op-id")

        await orchestrator.import_plays_two_phase(
            mock_importer,
            mock_uow,
            user_id="test-user",
            params=LastfmImportParams(),
            progress_emitter=emitter,
        )

        # Phase 1 may call start_operation via the importer, but Phase 2 should not
        # since we short-circuit on empty ingestion
        emitter.emit_progress.assert_not_awaited()


def _play_index(play: ConnectorTrackPlay) -> int:
    """Recover the ordinal a play was built with from its track name."""
    return int(play.track_name.rsplit(" ", 1)[1])


def _deterministic_outcome(
    plays: list[ConnectorTrackPlay],
) -> PlayResolutionOutcome:
    """Resolve as a pure function of the plays handed in.

    Every fourth play "fails" and every third resolves via fallback, so the
    totals are fixed by the play set alone — chunking may not change them.
    """
    resolved = [p for p in plays if _play_index(p) % 4 != 0]
    return PlayResolutionOutcome(
        track_plays=[
            _make_resolved_track_play(track_id=_play_index(p) + 1) for p in resolved
        ],
        metrics={
            "error_count": len(plays) - len(resolved),
            "fallback_resolved": len([p for p in plays if _play_index(p) % 3 == 0]),
        },
        resolutions=(),
    )


def _resolving_outcome(plays: list[ConnectorTrackPlay]) -> PlayResolutionOutcome:
    """Every play resolves and reports its ledger write-back pair."""
    return PlayResolutionOutcome(
        track_plays=[
            _make_resolved_track_play(track_id=_play_index(p) + 1) for p in plays
        ],
        metrics={"error_count": 0},
        resolutions=tuple((p, _RESOLVED_TRACK_ID) for p in plays),
    )


def _failing_outcome(plays: list[ConnectorTrackPlay]) -> PlayResolutionOutcome:
    """Every play fails, each with its own named failure record."""
    return PlayResolutionOutcome(
        track_plays=[],
        metrics={
            "error_count": len(plays),
            "resolution_failures": [
                {
                    "track": f"Artist - {p.track_name}",
                    "spotify_id": f"id-{_play_index(p)}",
                    "reason": "track_resolution_failed",
                }
                for p in plays
            ],
        },
        resolutions=(),
    )


class TestResolutionFailureAccumulation:
    """Per-item failures accumulate across chunks and services, bounded.

    The resolver reports failures per call and the orchestrator calls it once per
    chunk — reading only the last call's list would report a handful of failures
    from a run that had hundreds. The cap bounds the JSONB ``issues`` array the
    audit row carries.
    """

    async def test_failures_accumulate_across_chunks(
        self, orchestrator, mock_resolver, mock_uow
    ):
        # 3 chunks (50/50/20), all failing — the recorded list must span them.
        connector_plays = [_make_connector_play(f"Song {i}") for i in range(120)]
        mock_resolver.resolve_connector_plays.side_effect = (
            lambda plays, _uow, **_kwargs: _failing_outcome(plays)
        )

        result = await orchestrator.execute_resolution_phase(
            connector_plays,
            mock_uow,
            user_id="test-user",
            progress_emitter=NullProgressEmitter(),
        )

        failures = result.resolution_failures
        assert len(failures) == _MAX_RECORDED_RESOLUTION_FAILURES
        assert result.resolution_failures_truncated == 120 - len(failures)
        # Spans the first chunk's boundary rather than restarting per call.
        assert failures[0]["spotify_id"] == "id-0"
        assert failures[-1]["spotify_id"] == f"id-{len(failures) - 1}"

    async def test_under_the_cap_records_everything_untruncated(
        self, orchestrator, mock_resolver, mock_uow
    ):
        connector_plays = [_make_connector_play(f"Song {i}") for i in range(3)]
        mock_resolver.resolve_connector_plays.side_effect = (
            lambda plays, _uow, **_kwargs: _failing_outcome(plays)
        )

        result = await orchestrator.execute_resolution_phase(
            connector_plays,
            mock_uow,
            user_id="test-user",
            progress_emitter=NullProgressEmitter(),
        )

        assert len(result.resolution_failures) == 3
        assert result.resolution_failures_truncated == 0

    async def test_failures_survive_into_the_combined_result(
        self, orchestrator, mock_resolver, mock_uow, mock_importer
    ):
        # The SSE seam reads the COMBINED result; nested resolution_phase
        # metadata is opaque to it.
        connector_plays = [_make_connector_play("Song 0")]
        mock_importer.import_plays.return_value = (
            _make_ingestion_result(imported=1, raw_plays=1, duplicates=0),
            connector_plays,
        )
        mock_resolver.resolve_connector_plays.side_effect = (
            lambda plays, _uow, **_kwargs: _failing_outcome(plays)
        )

        result = await orchestrator.import_plays_two_phase(
            mock_importer, mock_uow, user_id="test-user", params=LastfmImportParams()
        )

        assert result.resolution_failures == [
            {
                "track": "Artist - Song 0",
                "spotify_id": "id-0",
                "reason": "track_resolution_failed",
            }
        ]

    async def test_clean_run_records_no_failures(
        self, orchestrator, mock_resolver, mock_uow
    ):
        connector_plays = [_make_connector_play(f"Song {i}") for i in range(3)]
        mock_resolver.resolve_connector_plays.side_effect = (
            lambda plays, _uow, **_kwargs: PlayResolutionOutcome(
                track_plays=[
                    _make_resolved_track_play(track_id=_play_index(p) + 1)
                    for p in plays
                ],
                metrics={"error_count": 0},
                resolutions=(),
            )
        )

        result = await orchestrator.execute_resolution_phase(
            connector_plays,
            mock_uow,
            user_id="test-user",
            progress_emitter=NullProgressEmitter(),
        )

        assert result.resolution_failures == []
        assert result.resolution_failures_truncated == 0


class TestChunkFlightRecording:
    """One structured line per chunk — the instrument the import is tuned by."""

    async def _flight_lines(self, orchestrator, mock_resolver, mock_uow, *, plays):
        connector_plays = [_make_connector_play(f"Song {i}") for i in range(plays)]
        mock_resolver.resolve_connector_plays.side_effect = (
            lambda chunk, _uow, **_kwargs: _deterministic_outcome(chunk)
        )

        with capture_logs() as entries:
            _ = await orchestrator.execute_resolution_phase(
                connector_plays,
                mock_uow,
                user_id="test-user",
                progress_emitter=NullProgressEmitter(),
            )

        return [e for e in entries if e["event"] == "play_import_chunk"]

    async def test_one_line_per_chunk(self, orchestrator, mock_resolver, mock_uow):
        lines = await self._flight_lines(
            orchestrator, mock_resolver, mock_uow, plays=120
        )

        assert [line["plays"] for line in lines] == [50, 50, 20]
        assert [line["chunk"] for line in lines] == [0, 1, 2]

    async def test_carries_the_fields_the_analysis_needs(
        self, orchestrator, mock_resolver, mock_uow
    ):
        (line,) = await self._flight_lines(
            orchestrator, mock_resolver, mock_uow, plays=10
        )

        assert line.keys() >= {
            "service",
            "wall_ms",
            "db_ms",
            "db_stmts",
            "off_db_ms",
            "rtt_ms",
            "api_calls",
            "resolve_ms",
            "commit_ms",
            "db_ops",
        }

    async def test_timings_are_internally_consistent(
        self, orchestrator, mock_resolver, mock_uow
    ):
        (line,) = await self._flight_lines(
            orchestrator, mock_resolver, mock_uow, plays=10
        )

        assert line["db_ms"] <= line["wall_ms"]
        assert line["resolve_ms"] <= line["wall_ms"]
        assert line["db_stmts"] >= 0
        assert line["off_db_ms"] == line["wall_ms"] - line["db_ms"]

    async def test_counters_restart_each_chunk(
        self, orchestrator, mock_resolver, mock_uow
    ):
        """Each chunk is its own span — counters are per-chunk, not cumulative."""
        lines = await self._flight_lines(
            orchestrator, mock_resolver, mock_uow, plays=120
        )

        assert all(line["db_stmts"] == lines[0]["db_stmts"] for line in lines)


class TestResolutionChunking:
    """Phase 2 commits per chunk so a large import cannot exhaust PostgreSQL's
    64-subtransaction cache, and so a crash costs at most one chunk of rework."""

    async def test_plays_resolved_in_chunks_of_fifty_each_committed(
        self, orchestrator, mock_resolver, mock_uow
    ):
        connector_plays = [_make_connector_play(f"Song {i}") for i in range(120)]
        mock_resolver.resolve_connector_plays.side_effect = (
            lambda plays, _uow, **_kwargs: _deterministic_outcome(plays)
        )

        await orchestrator.execute_resolution_phase(
            connector_plays,
            mock_uow,
            user_id="test-user",
            progress_emitter=NullProgressEmitter(),
        )

        chunk_sizes = [
            len(call.args[0])
            for call in mock_resolver.resolve_connector_plays.await_args_list
        ]
        assert chunk_sizes == [50, 50, 20]
        assert mock_uow.commit_batch.await_count == 3

    async def test_chunked_totals_match_single_call_resolution(
        self, orchestrator, mock_resolver, mock_uow
    ):
        """Chunking is a transaction boundary, not a semantic one: the resolved
        count and the summed error/fallback counts must be identical to what one
        resolver call over the whole batch would have produced."""
        connector_plays = [_make_connector_play(f"Song {i}") for i in range(120)]
        mock_resolver.resolve_connector_plays.side_effect = (
            lambda plays, _uow, **_kwargs: _deterministic_outcome(plays)
        )
        single_call = _deterministic_outcome(connector_plays)

        result = await orchestrator.execute_resolution_phase(
            connector_plays,
            mock_uow,
            user_id="test-user",
            progress_emitter=NullProgressEmitter(),
        )

        counts = result.to_counts()
        assert counts["total"] == 120
        assert counts["resolved"] == len(single_call.track_plays)
        assert counts["errors"] == single_call.metrics["error_count"]
        assert counts["fallback_resolved"] == single_call.metrics["fallback_resolved"]
        assert result.metadata["resolved_plays"] == len(single_call.track_plays)

    async def test_exact_chunk_size_stays_one_chunk(
        self, orchestrator, mock_resolver, mock_uow
    ):
        """50 plays is the boundary — one resolver call, one commit, no empty tail."""
        connector_plays = [_make_connector_play(f"Song {i}") for i in range(50)]
        mock_resolver.resolve_connector_plays.side_effect = (
            lambda plays, _uow, **_kwargs: _deterministic_outcome(plays)
        )

        await orchestrator.execute_resolution_phase(
            connector_plays,
            mock_uow,
            user_id="test-user",
            progress_emitter=NullProgressEmitter(),
        )

        mock_resolver.resolve_connector_plays.assert_awaited_once()
        assert (
            len(mock_resolver.resolve_connector_plays.await_args_list[0].args[0]) == 50
        )
        mock_uow.commit_batch.assert_awaited_once()

    async def test_chunk_failure_preserves_prior_chunk_commits(
        self, orchestrator, mock_resolver, mock_uow
    ):
        """Chunk 1's commit has already made its writes durable when chunk 2
        blows up, and the exception still reaches the caller's failure handling."""
        connector_plays = [_make_connector_play(f"Song {i}") for i in range(120)]
        boom = RuntimeError("resolver exploded on chunk 2")

        async def resolve(plays, _uow, **_kwargs):
            if _play_index(plays[0]) == 50:
                raise boom
            return _deterministic_outcome(plays)

        mock_resolver.resolve_connector_plays.side_effect = resolve

        with pytest.raises(RuntimeError) as exc_info:
            await orchestrator.execute_resolution_phase(
                connector_plays,
                mock_uow,
                user_id="test-user",
                progress_emitter=NullProgressEmitter(),
            )

        assert exc_info.value is boom
        mock_uow.commit_batch.assert_awaited_once()
        assert mock_resolver.resolve_connector_plays.await_count == 2

    async def test_progress_emitted_per_chunk(
        self, orchestrator, mock_resolver, mock_uow
    ):
        """A long import must show movement between chunks, not only per service."""
        connector_plays = [_make_connector_play(f"Song {i}") for i in range(120)]
        mock_resolver.resolve_connector_plays.side_effect = (
            lambda plays, _uow, **_kwargs: _deterministic_outcome(plays)
        )
        emitter = AsyncMock()
        emitter.start_operation = AsyncMock(return_value="phase2-op-id")

        await orchestrator.execute_resolution_phase(
            connector_plays,
            mock_uow,
            user_id="test-user",
            progress_emitter=emitter,
        )

        assert emitter.emit_progress.await_count == 3


class TestResolutionWriteBack:
    """Phase 2 persists connector-play↔track resolutions onto the ledger.

    Per chunk, inside the transaction that chunk's ``commit_batch`` makes
    durable — resolution durability has to match track durability. Accumulating
    the run's resolutions for one end-of-run write-back is what discarded all
    15,317 of them when a 54-minute import was killed mid-run.
    """

    async def test_resolutions_persisted_after_resolution(
        self, orchestrator, mock_resolver, mock_uow
    ):
        connector_plays = [_make_connector_play()]
        track_play = _make_resolved_track_play()
        resolutions = tuple((cp, track_play.track_id) for cp in connector_plays)
        mock_resolver.resolve_connector_plays.return_value = PlayResolutionOutcome(
            track_plays=[track_play],
            metrics={"error_count": 0},
            resolutions=resolutions,
        )

        await orchestrator.execute_resolution_phase(
            connector_plays,
            mock_uow,
            user_id="test-user",
            progress_emitter=NullProgressEmitter(),
        )

        repo = mock_uow.get_connector_play_repository.return_value
        repo.bulk_update_resolution.assert_awaited_once()
        args, kwargs = repo.bulk_update_resolution.await_args
        assert list(args[0]) == list(resolutions)
        assert kwargs["resolved_at"] is not None

    async def test_no_write_back_without_resolutions(
        self, orchestrator, mock_resolver, mock_uow
    ):
        """Unresolvable batches leave the ledger untouched (rows stay NULL)."""
        connector_plays = [_make_connector_play()]
        mock_resolver.resolve_connector_plays.return_value = PlayResolutionOutcome(
            track_plays=[_make_resolved_track_play()],
            metrics={"error_count": 0},
            resolutions=(),
        )

        await orchestrator.execute_resolution_phase(
            connector_plays,
            mock_uow,
            user_id="test-user",
            progress_emitter=NullProgressEmitter(),
        )

        repo = mock_uow.get_connector_play_repository.return_value
        repo.bulk_update_resolution.assert_not_awaited()

    async def test_write_back_runs_once_per_chunk_with_that_chunks_resolutions(
        self, orchestrator, mock_resolver, mock_uow
    ):
        """Each chunk stamps its own plays — never the run's accumulated set."""
        connector_plays = [_make_connector_play(f"Song {i}") for i in range(120)]
        mock_resolver.resolve_connector_plays.side_effect = (
            lambda plays, _uow, **_kwargs: _resolving_outcome(plays)
        )

        await orchestrator.execute_resolution_phase(
            connector_plays,
            mock_uow,
            user_id="test-user",
            progress_emitter=NullProgressEmitter(),
        )

        repo = mock_uow.get_connector_play_repository.return_value
        stamped = [
            [play for play, _ in call.args[0]]
            for call in repo.bulk_update_resolution.await_args_list
        ]
        assert stamped == [
            connector_plays[0:50],
            connector_plays[50:100],
            connector_plays[100:120],
        ]

    async def test_chunk_failure_preserves_earlier_chunks_stamped_resolutions(
        self, orchestrator, mock_resolver, mock_uow
    ):
        """Chunks 1-2 are stamped AND committed before chunk 3 blows up.

        The journal is the whole point: a write-back that lands after the last
        chunk's commit — or after the loop — is not durable when the process
        dies, which is exactly the incident.
        """
        connector_plays = [_make_connector_play(f"Song {i}") for i in range(120)]
        boom = RuntimeError("resolver exploded on chunk 3")
        journal: list[str] = []

        async def resolve(plays, _uow, **_kwargs):
            if _play_index(plays[0]) == 100:
                raise boom
            return _resolving_outcome(plays)

        async def stamp(resolutions, *, resolved_at):
            _ = resolved_at
            journal.append(f"stamp:{_play_index(resolutions[0][0])}")
            return len(resolutions)

        mock_resolver.resolve_connector_plays.side_effect = resolve
        repo = mock_uow.get_connector_play_repository.return_value
        repo.bulk_update_resolution.side_effect = stamp
        mock_uow.commit_batch.side_effect = lambda: journal.append("commit")

        with pytest.raises(RuntimeError) as exc_info:
            await orchestrator.execute_resolution_phase(
                connector_plays,
                mock_uow,
                user_id="test-user",
                progress_emitter=NullProgressEmitter(),
            )

        assert exc_info.value is boom
        assert journal == ["stamp:0", "commit", "stamp:50", "commit"]


class _RecordingSubscriber:
    """Captures everything the broker actually publishes.

    Events the progress ledger rejects never reach a subscriber, so "was it
    delivered" is the only honest test of the monotonicity contract.
    """

    def __init__(self) -> None:
        self.operations: list[ProgressOperation] = []
        self.events: list[ProgressEvent] = []

    async def on_operation_started(self, operation: ProgressOperation) -> None:
        self.operations.append(operation)

    async def on_progress_event(self, event: ProgressEvent) -> None:
        self.events.append(event)

    async def on_operation_completed(
        self, operation_id: str, final_status: OperationStatus
    ) -> None:
        return None


_PROJECTION_DAYS = (
    datetime(2024, 6, 15, 14, 30, tzinfo=UTC),
    datetime(2024, 6, 16, 14, 30, tzinfo=UTC),
    datetime(2024, 6, 17, 14, 30, tzinfo=UTC),
)


class TestProjectionProgressOperation:
    """Phase 3 emits under its OWN operation, not the resolution phase's.

    The progress ledger enforces monotonic ``current`` per operation id and
    drops anything that goes backwards. The projection's chunk counter restarts
    at 1, so sharing the resolution operation (already at one event per resolved
    play) silently discarded every projection event and logged a warning for
    each — thousands of log lines and a frozen bar for the whole tail.
    """

    @staticmethod
    def _plays_across_days(count: int = 60) -> list[ConnectorTrackPlay]:
        return [
            _make_connector_play(f"Song {i}", _PROJECTION_DAYS[i % 3])
            for i in range(count)
        ]

    @staticmethod
    async def _run(orchestrator, mock_resolver, mock_uow, connector_plays):
        mock_resolver.resolve_connector_plays.side_effect = (
            lambda plays, _uow, **_kwargs: _resolving_outcome(plays)
        )
        broker = ProgressBroker()
        subscriber = _RecordingSubscriber()
        _ = await broker.subscribe(subscriber)

        await orchestrator.execute_resolution_phase(
            connector_plays, mock_uow, user_id="test-user", progress_emitter=broker
        )
        return subscriber

    async def test_projection_events_are_delivered_and_start_from_one(
        self, orchestrator, mock_resolver, mock_uow
    ):
        subscriber = await self._run(
            orchestrator, mock_resolver, mock_uow, self._plays_across_days()
        )

        projection_op = next(
            operation
            for operation in subscriber.operations
            if "Projecting" in operation.description
        )
        projection_events = [
            event
            for event in subscriber.events
            if event.operation_id == projection_op.operation_id
        ]

        # One event per touched day, monotonic from 1, none dropped.
        assert [event.current for event in projection_events] == [1, 2, 3]
        assert {event.total for event in projection_events} == {3}

    async def test_projection_operation_is_distinct_from_the_resolution_one(
        self, orchestrator, mock_resolver, mock_uow
    ):
        subscriber = await self._run(
            orchestrator, mock_resolver, mock_uow, self._plays_across_days()
        )

        descriptions = [operation.description for operation in subscriber.operations]
        assert len(set(descriptions)) == 2
        resolution_events = [
            event
            for event in subscriber.events
            if event.operation_id == subscriber.operations[0].operation_id
        ]
        # The resolution meter is far past the projection's counter — sharing
        # its id is exactly what dropped every projection event.
        assert max(event.current for event in resolution_events) == 60

    async def test_projection_chunks_only_the_days_the_batch_touched(
        self, orchestrator, mock_resolver, mock_uow
    ):
        """A stray old play must not drag the batch through the empty span."""
        connector_plays = [
            *self._plays_across_days(3),
            _make_connector_play("Song 99", datetime(2011, 3, 5, 14, 30, tzinfo=UTC)),
        ]

        subscriber = await self._run(
            orchestrator, mock_resolver, mock_uow, connector_plays
        )

        projection_op = next(
            operation
            for operation in subscriber.operations
            if "Projecting" in operation.description
        )
        totals = {
            event.total
            for event in subscriber.events
            if event.operation_id == projection_op.operation_id
        }
        assert totals == {4}  # four touched days, not 5,000 calendar days
