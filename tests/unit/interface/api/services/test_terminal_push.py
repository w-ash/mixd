"""``push_terminal_best_effort`` — the one terminal push every stream uses.

A terminal frame is built from caller-supplied fields and validated on the way
out, so building it can fail. It is also pushed from a ``finally`` that still
owes its operation a coordinator completion, a concurrency slot, a callback or
the SSE teardown. The helper therefore logs a rejected payload instead of
raising, and the workflow executors — which push outside ``run_sse_operation``
— must inherit exactly that.
"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import structlog

from src.config.constants import WorkflowConstants
import src.interface.api.services.sse_operations as sse_ops
import src.interface.api.services.workflow_execution as wf_exec


@asynccontextmanager
async def _null_track_run():
    yield


def _rejected_payload(*_args: object, **_kwargs: object) -> object:
    """What ``build_terminal_event`` does with a field its schema forbids."""
    raise ValueError("not_a_field")


def _drain(queue: asyncio.Queue[object]) -> list[object]:
    return [queue.get_nowait() for _ in range(queue.qsize())]


@pytest.fixture
def grace_calls(monkeypatch) -> list[float | None]:
    """Capture the grace each workflow finalize call was given."""
    calls: list[float | None] = []

    async def _capture(
        _operation_id: str, *, grace_period_seconds: float | None = None
    ) -> None:
        calls.append(grace_period_seconds)

    monkeypatch.setattr(wf_exec, "finalize_sse_operation", _capture)
    return calls


class TestPushTerminalBestEffort:
    async def test_valid_frame_reaches_the_queue(self):
        queue: asyncio.Queue[object] = asyncio.Queue()

        await sse_ops.push_terminal_best_effort(
            queue,
            "evt_final",
            WorkflowConstants.SSE_EVENT_COMPLETE,
            "op-1",
            "completed",
            operation_type="workflow_run",
            counts={"imported": 3},
        )

        frame = _drain(queue)
        assert len(frame) == 1
        assert frame[0]["event"] == "complete"
        assert frame[0]["data"]["counts"] == {"imported": 3}
        assert frame[0]["data"]["touched"]

    async def test_rejected_payload_is_logged_not_raised(self, monkeypatch):
        queue: asyncio.Queue[object] = asyncio.Queue()
        monkeypatch.setattr(sse_ops, "build_terminal_event", _rejected_payload)

        with structlog.testing.capture_logs() as logs:
            await sse_ops.push_terminal_best_effort(
                queue, "evt_final", "complete", "op-1", "completed"
            )

        assert _drain(queue) == []
        assert any(
            entry["log_level"] == "error"
            and entry["event"] == "Failed to push terminal SSE event"
            and entry["operation_id"] == "op-1"
            for entry in logs
        )

    async def test_log_context_names_the_stream_at_fault(self, monkeypatch):
        """A push onto someone else's stream needs more than its own id."""
        queue: asyncio.Queue[object] = asyncio.Queue()
        monkeypatch.setattr(sse_ops, "build_terminal_event", _rejected_payload)

        with structlog.testing.capture_logs() as logs:
            await sse_ops.push_terminal_best_effort(
                queue,
                "evt_7",
                "sub_operation_completed",
                "op-child",
                "completed",
                log_message="Failed to push terminal SSE event to ancestor stream",
                log_context={"stream_operation_id": "op-parent"},
            )

        assert any(
            entry["event"] == "Failed to push terminal SSE event to ancestor stream"
            and entry["stream_operation_id"] == "op-parent"
            for entry in logs
        )


class TestWorkflowTerminalPushIsBestEffort:
    """The workflow executors push their own terminals, outside
    ``run_sse_operation``. A rejected payload must not skip the teardown."""

    async def test_run_terminal_failure_still_finalizes(self, monkeypatch, grace_calls):
        monkeypatch.setattr(sse_ops, "build_terminal_event", _rejected_payload)
        monkeypatch.setattr(wf_exec, "track_run", _null_track_run)
        monkeypatch.setattr(
            "src.application.use_cases.workflow_runs.ExecuteWorkflowRunUseCase.execute",
            AsyncMock(
                return_value=MagicMock(
                    status=WorkflowConstants.RUN_STATUS_COMPLETED,
                    output_track_count=0,
                    duration_ms=1,
                )
            ),
        )
        queue: asyncio.Queue[object] = asyncio.Queue()

        with structlog.testing.capture_logs() as logs:
            await wf_exec.execute_workflow_background(
                "op-run", MagicMock(), uuid4(), queue, "user"
            )

        assert _drain(queue) == []
        assert grace_calls == [None]
        assert any(
            entry["event"] == "Failed to push terminal SSE event" for entry in logs
        )

    async def test_preview_terminal_failure_still_releases_the_slot(
        self, monkeypatch, grace_calls
    ):
        monkeypatch.setattr(sse_ops, "build_terminal_event", _rejected_payload)
        monkeypatch.setattr(
            "src.application.use_cases.workflow_preview.PreviewWorkflowUseCase.execute",
            AsyncMock(
                return_value=MagicMock(
                    output_tracks=[],
                    total_track_count=0,
                    metric_columns=[],
                    node_summaries=[],
                    duration_ms=1,
                )
            ),
        )
        sse_ops.acquire_operation_slot("op-preview")

        with structlog.testing.capture_logs() as logs:
            await wf_exec.execute_preview_background(
                "op-preview", MagicMock(), asyncio.Queue(), "user"
            )

        assert grace_calls == [None]
        assert "op-preview" not in sse_ops._active_operations
        assert any(
            entry["event"] == "Failed to push terminal SSE event" for entry in logs
        )
