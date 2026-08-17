"""The background executors' cancellation contract.

On cancellation both must skip the SSE read window (the 30s default grace per
task would blow the 15s shutdown drain) and re-raise so the task ends
cancelled — mirroring ``run_sse_operation``.
"""

import asyncio
from asyncio import CancelledError
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

import src.interface.api.services.sse_operations as sse_ops
import src.interface.api.services.workflow_execution as wf_exec


@asynccontextmanager
async def _null_track_run():
    yield


@pytest.fixture
def grace_calls(monkeypatch) -> list[float | None]:
    """Capture the grace each finalize call was given."""
    calls: list[float | None] = []

    async def _capture(
        _operation_id: str, *, grace_period_seconds: float | None = None
    ) -> None:
        calls.append(grace_period_seconds)

    monkeypatch.setattr(wf_exec, "finalize_sse_operation", _capture)
    return calls


class TestCancellationSkipsGraceAndReraises:
    async def test_workflow_run_background(self, monkeypatch, grace_calls):
        monkeypatch.setattr(wf_exec, "track_run", _null_track_run)
        monkeypatch.setattr(
            wf_exec,
            "_run_workflow_and_push_terminal",
            AsyncMock(side_effect=CancelledError()),
        )

        with pytest.raises(CancelledError):
            await wf_exec.execute_workflow_background(
                "op-run", MagicMock(), uuid4(), asyncio.Queue(), "user"
            )

        assert grace_calls == [0.0]

    async def test_preview_background(self, monkeypatch, grace_calls):
        monkeypatch.setattr(
            "src.application.use_cases.workflow_preview.PreviewWorkflowUseCase.execute",
            AsyncMock(side_effect=CancelledError()),
        )
        sse_ops.acquire_operation_slot("op-preview")

        with pytest.raises(CancelledError):
            await wf_exec.execute_preview_background(
                "op-preview", MagicMock(), asyncio.Queue(), "user"
            )

        assert grace_calls == [0.0]
        assert "op-preview" not in sse_ops._active_operations

    async def test_clean_preview_keeps_the_default_grace(
        self, monkeypatch, grace_calls
    ):
        result = MagicMock(
            output_tracks=[],
            total_track_count=0,
            metric_columns=[],
            node_summaries=[],
            duration_ms=1,
        )
        monkeypatch.setattr(
            "src.application.use_cases.workflow_preview.PreviewWorkflowUseCase.execute",
            AsyncMock(return_value=result),
        )

        await wf_exec.execute_preview_background(
            "op-clean", MagicMock(), asyncio.Queue(), "user"
        )

        assert grace_calls == [None]
