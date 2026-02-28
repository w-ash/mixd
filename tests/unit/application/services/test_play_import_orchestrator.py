"""Unit tests for PlayImportOrchestrator two-phase workflow.

Tests the orchestration layer: phase coordination, result combination,
short-circuit on empty ingestion, and error handling.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.services.play_import_orchestrator import PlayImportOrchestrator
from src.domain.entities import ConnectorTrackPlay, OperationResult, TrackPlay


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


def _make_connector_play(track_name: str = "Test Song") -> ConnectorTrackPlay:
    """Create a ConnectorTrackPlay for testing."""
    return ConnectorTrackPlay(
        service="spotify",
        track_name=track_name,
        artist_name="Test Artist",
        album_name="Test Album",
        played_at=datetime(2024, 6, 15, 14, 30, tzinfo=UTC),
        ms_played=240000,
        service_metadata={"track_uri": "spotify:track:abc123def456ghi789jk"},
        import_timestamp=datetime(2024, 7, 1, tzinfo=UTC),
        import_source="spotify_export",
        import_batch_id="test-batch",
    )


@pytest.fixture
def orchestrator():
    return PlayImportOrchestrator()


@pytest.fixture
def mock_uow():
    uow = MagicMock()
    plays_repo = AsyncMock()
    plays_repo.bulk_insert_plays.return_value = (5, 0)
    uow.get_plays_repository.return_value = plays_repo
    return uow


@pytest.fixture
def mock_importer():
    return AsyncMock()


class TestTwoPhaseHappyPath:
    """Test the normal two-phase workflow."""

    @pytest.mark.asyncio
    async def test_ingestion_then_resolution(
        self, orchestrator, mock_uow, mock_importer
    ):
        """Happy path: ingestion returns plays → resolution resolves them."""
        connector_plays = [_make_connector_play(f"Song {i}") for i in range(3)]
        mock_importer.import_plays.return_value = (
            _make_ingestion_result(imported=3, raw_plays=3, duplicates=0),
            connector_plays,
        )

        # Mock the resolver that _execute_resolution_phase calls
        mock_resolver = AsyncMock()
        mock_resolver.resolve_connector_plays.return_value = (
            [MagicMock(spec=TrackPlay) for _ in range(3)],
            {"error_count": 0},
        )

        with patch.object(
            orchestrator, "_get_play_resolver", return_value=mock_resolver
        ):
            result = await orchestrator.import_plays_two_phase(
                mock_importer, mock_uow, file_path="/fake/path.json"
            )

        assert result.operation_name == "Two-Phase Play Import"
        # Ingestion should have been called
        mock_importer.import_plays.assert_called_once()

    @pytest.mark.asyncio
    async def test_combined_result_has_both_phase_metadata(
        self, orchestrator, mock_uow, mock_importer
    ):
        connector_plays = [_make_connector_play()]
        mock_importer.import_plays.return_value = (
            _make_ingestion_result(imported=1, raw_plays=1, duplicates=0),
            connector_plays,
        )

        mock_resolver = AsyncMock()
        mock_resolver.resolve_connector_plays.return_value = (
            [MagicMock(spec=TrackPlay)],
            {"error_count": 0},
        )

        with patch.object(
            orchestrator, "_get_play_resolver", return_value=mock_resolver
        ):
            result = await orchestrator.import_plays_two_phase(mock_importer, mock_uow)

        assert "ingestion_phase" in result.metadata
        assert "resolution_phase" in result.metadata
        assert result.metadata["ingestion_phase"]["batch_id"] == "test-batch-123"


class TestEmptyIngestion:
    """Test short-circuit when ingestion produces no plays."""

    @pytest.mark.asyncio
    async def test_no_plays_short_circuits(self, orchestrator, mock_uow, mock_importer):
        """Empty ingestion should return early without resolution phase."""
        ingestion_result = _make_ingestion_result(imported=0, raw_plays=0, duplicates=0)
        mock_importer.import_plays.return_value = (ingestion_result, [])

        result = await orchestrator.import_plays_two_phase(mock_importer, mock_uow)

        # Should return the ingestion result directly
        assert result is ingestion_result
        # Resolution phase should not be attempted
        mock_uow.get_plays_repository.assert_not_called()


class TestResolutionPhaseErrors:
    """Test error handling in the resolution phase."""

    @pytest.mark.asyncio
    async def test_resolution_errors_captured_in_metrics(
        self, orchestrator, mock_uow, mock_importer
    ):
        connector_plays = [_make_connector_play()]
        mock_importer.import_plays.return_value = (
            _make_ingestion_result(imported=1, raw_plays=1, duplicates=0),
            connector_plays,
        )

        mock_resolver = AsyncMock()
        mock_resolver.resolve_connector_plays.return_value = (
            [],  # No resolved plays
            {"error_count": 1},  # 1 error
        )

        with patch.object(
            orchestrator, "_get_play_resolver", return_value=mock_resolver
        ):
            result = await orchestrator.import_plays_two_phase(mock_importer, mock_uow)

        # The resolution phase error should be in combined metrics
        assert result.metadata["resolution_phase"]["error_count"] == 1


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
