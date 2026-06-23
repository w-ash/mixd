"""Characterization tests for the SSE operation seam (``run_sse_operation``).

Pins the result→audit-row mapping the web relies on: the use case's
returned ``OperationResult`` decides the ``OperationRun``'s terminal status and
counts. A handled soft failure (``is_failure``) records as ``error`` with real
counts — not a blanket ``complete`` — so a failed overnight run is visible the
next time the user opens mixd. This was the dropped-result seam: ``run_sse_operation``
used to ``await coro`` and discard the result, finalizing every clean return as
``complete`` with no counts.
"""

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from src.domain.entities.operations import OperationResult
from src.interface.api.services import sse_operations
from src.interface.api.services.progress import (
    OperationBoundEmitter,
    get_operation_registry,
)


def _drain(queue) -> list[dict]:
    events: list[dict] = []
    while not queue.empty():
        item = queue.get_nowait()
        if isinstance(item, dict):
            events.append(item)
    return events


@pytest.fixture
def captured_finalize():
    """Patch the audit-write + SSE cleanup so we can read what the seam recorded."""
    with (
        patch.object(sse_operations, "finalize_run", new=AsyncMock()) as finalize,
        patch.object(sse_operations, "finalize_sse_operation", new=AsyncMock()),
    ):
        yield finalize


def _op_id() -> str:
    # Unique id per test so the module-global operation registry can't collide.
    return f"op-{uuid4()}"


class TestRunSseOperationAuditOutcome:
    """run_sse_operation maps the returned OperationResult to the audit row."""

    async def test_clean_result_finalizes_complete_with_counts(self, captured_finalize):
        result = OperationResult(operation_name="Import")
        result.summary_metrics.add("track_plays", 42, "Plays Imported")

        async def coro() -> OperationResult:
            return result

        await sse_operations.run_sse_operation(
            _op_id(), coro(), run_id=uuid4(), user_id="u1"
        )

        captured_finalize.assert_awaited_once()
        kwargs = captured_finalize.await_args.kwargs
        assert kwargs["status"] == "complete"
        assert kwargs["counts"] == {"track_plays": 42}

    async def test_soft_failure_result_finalizes_error_with_counts(
        self, captured_finalize
    ):
        # The F1/F4-class bug: the use case handled the error and returned a
        # soft-failure result; the seam used to record this as 'complete'.
        result = OperationResult(operation_name="Import")
        result.summary_metrics.add("errors", 1, "Errors", significance=1)
        result.metadata["error"] = "Last.fm timed out"

        async def coro() -> OperationResult:
            return result

        await sse_operations.run_sse_operation(
            _op_id(), coro(), run_id=uuid4(), user_id="u1"
        )

        kwargs = captured_finalize.await_args.kwargs
        assert kwargs["status"] == "error"
        assert kwargs["counts"]["errors"] == 1

    async def test_uncaught_exception_finalizes_error(self, captured_finalize):
        async def coro() -> None:
            raise RuntimeError("boom")

        await sse_operations.run_sse_operation(
            _op_id(), coro(), run_id=uuid4(), user_id="u1"
        )

        kwargs = captured_finalize.await_args.kwargs
        assert kwargs["status"] == "error"

    async def test_non_operation_result_stays_complete_without_counts(
        self, captured_finalize
    ):
        # Some coros (e.g. fire-and-forget) return None — no failure signal,
        # no counts, so the run stays complete.
        async def coro() -> None:
            return None

        await sse_operations.run_sse_operation(
            _op_id(), coro(), run_id=uuid4(), user_id="u1"
        )

        kwargs = captured_finalize.await_args.kwargs
        assert kwargs["status"] == "complete"
        assert kwargs["counts"] is None

    async def test_without_run_id_no_audit_write(self, captured_finalize):
        async def coro() -> OperationResult:
            return OperationResult(operation_name="x")

        # No run_id/user_id pair → the seam doesn't touch the audit log.
        await sse_operations.run_sse_operation(_op_id(), coro())

        captured_finalize.assert_not_awaited()


class TestRunSseOperationTerminalEvent:
    """run_sse_operation owns the LIVE terminal event with the run's status + counts.

    1a fixed the durable audit row; this is 1b — the live SSE toast. The
    use case's own operations are now children (they emit ``sub_*`` only), so the
    terminal ``complete``/``error`` must come from the seam itself, carrying the
    same ``OperationResult`` counts the audit row records.
    """

    async def test_clean_result_pushes_complete_event_with_counts(
        self, captured_finalize
    ):
        registry = get_operation_registry()
        op_id = _op_id()
        queue = await registry.register(op_id)
        try:
            result = OperationResult(operation_name="Import")
            result.summary_metrics.add("track_plays", 7, "Plays Imported")

            async def coro() -> OperationResult:
                return result

            await sse_operations.run_sse_operation(
                op_id, coro(), run_id=uuid4(), user_id="u1"
            )

            terminal = [
                e for e in _drain(queue) if e.get("event") in ("complete", "error")
            ]
            assert len(terminal) == 1
            assert terminal[0]["event"] == "complete"
            assert terminal[0]["data"]["final_status"] == "completed"
            assert terminal[0]["data"]["counts"] == {"track_plays": 7}
        finally:
            await registry.unregister(op_id)

    async def test_soft_failure_pushes_error_event_with_counts(self, captured_finalize):
        registry = get_operation_registry()
        op_id = _op_id()
        queue = await registry.register(op_id)
        try:
            result = OperationResult(operation_name="Import")
            result.summary_metrics.add("errors", 1, "Errors", significance=1)
            result.metadata["error"] = "Last.fm timed out"

            async def coro() -> OperationResult:
                return result

            await sse_operations.run_sse_operation(
                op_id, coro(), run_id=uuid4(), user_id="u1"
            )

            terminal = [
                e for e in _drain(queue) if e.get("event") in ("complete", "error")
            ]
            assert len(terminal) == 1
            assert terminal[0]["event"] == "error"
            assert terminal[0]["data"]["final_status"] == "failed"
            assert terminal[0]["data"]["counts"]["errors"] == 1
        finally:
            await registry.unregister(op_id)

    async def test_uncaught_exception_pushes_error_event(self, captured_finalize):
        registry = get_operation_registry()
        op_id = _op_id()
        queue = await registry.register(op_id)
        try:

            async def coro() -> None:
                raise RuntimeError("boom")

            await sse_operations.run_sse_operation(
                op_id, coro(), run_id=uuid4(), user_id="u1"
            )

            terminal = [
                e for e in _drain(queue) if e.get("event") in ("complete", "error")
            ]
            assert len(terminal) == 1
            assert terminal[0]["event"] == "error"
            assert "error_message" in terminal[0]["data"]["counts"]
        finally:
            await registry.unregister(op_id)


class TestLaunchSseOperationThreadsResult:
    """launch_sse_operation must thread the factory's RETURNED result into
    run_sse_operation — the dropped-result bug where every route factory was
    typed ``-> None`` and awaited without returning, so a soft-failure
    OperationResult never reached the audit row.
    """

    async def test_factory_result_reaches_run_sse_operation(self):
        failed = OperationResult(operation_name="Import")
        failed.summary_metrics.add("errors", 1, "Errors", significance=1)

        async def _factory(_emitter: OperationBoundEmitter) -> object:
            return failed

        seen_result: list[object] = []
        seen_factory: list[Callable[[], Awaitable[None]]] = []

        async def _fake_run(
            _operation_id: str,
            coro: Awaitable[object],
            *,
            run_id: UUID | None = None,
            user_id: str | None = None,
            description: str = "Operation",
        ) -> None:
            seen_result.append(await coro)

        def _fake_launch_background(
            _name: str, factory: Callable[[], Awaitable[None]]
        ) -> None:
            seen_factory.append(factory)

        with (
            patch.object(
                sse_operations, "start_run", new=AsyncMock(return_value=uuid4())
            ),
            patch.object(
                sse_operations, "launch_background", new=_fake_launch_background
            ),
            patch.object(sse_operations, "run_sse_operation", new=_fake_run),
            patch.object(sse_operations, "get_progress_broker", new=MagicMock()),
        ):
            await sse_operations.launch_sse_operation(
                user_id="u1",
                operation_type="import_lastfm_history",
                coro_factory=_factory,
            )
            # launch_background captured the lambda; drive it to run the seam.
            await seen_factory[0]()

        # A factory that awaited-without-returning would put None here.
        assert seen_result == [failed]

    async def test_request_params_threaded_to_start_run(self):
        """request_params reaches the kickoff audit-row write, so a retryable
        operation can be re-invoked from the run alone (connector config only)."""

        async def _factory(_emitter: OperationBoundEmitter) -> object:
            return OperationResult(operation_name="Import")

        start_run_mock = AsyncMock(return_value=uuid4())
        with (
            patch.object(sse_operations, "start_run", new=start_run_mock),
            patch.object(
                sse_operations, "launch_background", new=lambda _name, _factory: None
            ),
            patch.object(sse_operations, "get_progress_broker", new=MagicMock()),
        ):
            await sse_operations.launch_sse_operation(
                user_id="u1",
                operation_type="import_connector_playlists",
                coro_factory=_factory,
                request_params={
                    "connector_name": "spotify",
                    "sync_direction": "pull",
                },
            )

        start_run_mock.assert_awaited_once()
        assert start_run_mock.await_args.kwargs["request_params"] == {
            "connector_name": "spotify",
            "sync_direction": "pull",
        }
