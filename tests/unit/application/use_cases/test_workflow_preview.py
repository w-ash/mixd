"""Unit tests for workflow preview (dry-run) use case.

Tests PreviewWorkflowUseCase: happy path, invalid definition, and dry_run flag
propagation. Previews are not subject to the active-run concurrency guard
(no run row, no external writes).
"""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.use_cases.workflow_preview import PreviewWorkflowUseCase
from tests.fixtures import make_tracks, make_workflow_def


class _SteppingClock:
    """Stand-in for the timer's ``datetime``: each ``now()`` is 250 ms later."""

    def __init__(self) -> None:
        self._now = datetime(2026, 1, 1, tzinfo=UTC)

    def now(self, tz=None):
        current = self._now
        self._now += timedelta(milliseconds=250)
        return current


@contextmanager
def _patch_preview_deps(*, mock_run_return=None):
    """Patch dependencies imported at call time by PreviewWorkflowUseCase."""
    mock_observer = MagicMock()
    mock_observer.get_summaries.return_value = []

    with (
        patch("src.application.use_cases.workflow_preview.logger") as mock_logger,
        patch("src.application.services.progress_broker.get_progress_broker"),
        patch(
            "src.application.workflows.engine.observers.PreviewNodeObserver",
            return_value=mock_observer,
        ),
        patch(
            "src.application.workflows.engine.executor.run_workflow",
            new_callable=AsyncMock,
        ) as mock_run,
    ):
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=None)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_logger.contextualize.return_value = mock_ctx
        mock_logger.bind.return_value = mock_logger

        if mock_run_return is not None:
            mock_run.return_value = mock_run_return
        else:
            mock_run.return_value = MagicMock(tracks=[])

        yield mock_logger, mock_run, mock_observer


class TestPreviewWorkflowUseCase:
    """PreviewWorkflowUseCase executes a dry-run."""

    async def test_happy_path_returns_result(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "src.application.utilities.timing.datetime", _SteppingClock()
        )
        workflow_def = make_workflow_def()
        tracks = make_tracks(count=5)

        with _patch_preview_deps(mock_run_return=MagicMock(tracks=tracks, metrics={})):
            result = await PreviewWorkflowUseCase().execute(workflow_def)

        assert len(result.output_tracks) == 5
        assert result.output_tracks[0]["rank"] == 1
        assert result.duration_ms == 250
        assert result.metric_columns == []

    async def test_includes_metric_columns(self) -> None:
        """Preview result includes metric columns when workflow produces metrics."""
        workflow_def = make_workflow_def()
        tracks = make_tracks(count=3)
        metrics = {"playcount": {t.id: i * 10 for i, t in enumerate(tracks)}}

        with _patch_preview_deps(
            mock_run_return=MagicMock(tracks=tracks, metrics=metrics)
        ):
            result = await PreviewWorkflowUseCase().execute(workflow_def)

        assert result.metric_columns == ["playcount"]
        assert result.output_tracks[0]["metrics"]["playcount"] == 0

    async def test_dry_run_flag_propagated(self) -> None:
        """Verify run_workflow is called with dry_run=True."""
        workflow_def = make_workflow_def()

        with _patch_preview_deps() as (_logger, mock_run, _observer):
            await PreviewWorkflowUseCase().execute(workflow_def)

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs.get("dry_run") is True

    async def test_invalid_definition_raises(self) -> None:
        """Empty definition raises ValueError before execution."""
        from src.domain.entities.workflow import WorkflowDef

        empty_def = WorkflowDef(id="empty", name="Empty", tasks=[])

        with pytest.raises(ValueError, match="no tasks"):
            await PreviewWorkflowUseCase().execute(empty_def)

    async def test_output_tracks_limited(self) -> None:
        """The preview shows the first 20 tracks but reports the full count."""
        workflow_def = make_workflow_def()
        tracks = make_tracks(count=30)

        with _patch_preview_deps(mock_run_return=MagicMock(tracks=tracks, metrics={})):
            result = await PreviewWorkflowUseCase().execute(workflow_def)

        assert [t["title"] for t in result.output_tracks] == [
            "Track " + str(i) for i in range(1, 21)
        ]
        assert result.total_track_count == 30

    async def test_execution_error_propagates(self) -> None:
        """Exceptions during execution are re-raised."""
        workflow_def = make_workflow_def()

        with _patch_preview_deps() as (_logger, mock_run, _observer):
            mock_run.side_effect = RuntimeError("API timeout")
            with pytest.raises(RuntimeError, match="API timeout"):
                await PreviewWorkflowUseCase().execute(workflow_def)
