"""Characterization tests for the SSE operation seam (``run_sse_operation``).

Pins that the seam threads the use case's returned ``OperationResult`` into the
``OperationRun`` — status, counts, and issues, in one ``finalize_run`` call —
plus the two things the seam alone owns: the uncaught-exception path and the
live terminal event. This was the dropped-result seam: ``run_sse_operation`` used
to ``await coro`` and discard the result, finalizing every clean return as
``complete`` with no counts.

The result→outcome truth table itself lives with the mapping, in
``tests/unit/application/services/test_operation_outcome.py`` — it is shared with
the scheduled/demand path and must not be pinned per consumer.
"""

import asyncio
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
    """Patch the audit-write + SSE cleanup so we can read what the seam recorded.

    Status, counts, and the failure issue all ride one ``finalize_run`` call —
    they are written in a single transaction, so one mock reads all three.
    """
    with (
        patch.object(sse_operations, "finalize_run", new=AsyncMock()) as finalize,
        patch.object(sse_operations, "finalize_sse_operation", new=AsyncMock()),
    ):
        yield finalize


def _op_id() -> str:
    # Unique id per test so the module-global operation registry can't collide.
    return f"op-{uuid4()}"


class TestRunSseOperationAuditOutcome:
    """run_sse_operation threads ``audit_outcome`` into the audit row.

    One case per branch of the seam's own guard (clean → no issues, ``error`` and
    ``partial`` → issues), not per branch of the mapping.
    """

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
        assert kwargs["issues"] == []

    async def test_soft_failure_result_finalizes_error_with_counts_and_reason(
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
        # Status, counts and reason ride ONE call — a crash between two writes
        # would durably finalize a failed run with an empty issues array.
        assert kwargs["issues"] == [{"message": "Last.fm timed out"}]

    async def test_partial_result_finalizes_partial_with_per_item_issues(
        self, captured_finalize
    ):
        # `partial` is in FAILED_STATUSES, so the seam's issues guard must fire
        # for it too — this is the row that names the unresolved tracks.
        result = OperationResult(operation_name="Import")
        result.summary_metrics.add("track_plays", 98, "Track Plays Created")
        result.summary_metrics.add("errors", 1, "Errors", significance=1)
        result.metadata["error"] = "1 of 99 plays failed import"
        result.metadata["resolution_failures"] = [
            {"track": "Aphex Twin - Xtal", "reason": "track_resolution_failed"}
        ]

        async def coro() -> OperationResult:
            return result

        await sse_operations.run_sse_operation(
            _op_id(), coro(), run_id=uuid4(), user_id="u1"
        )

        kwargs = captured_finalize.await_args.kwargs
        assert kwargs["status"] == "partial"
        assert kwargs["counts"] == {"track_plays": 98, "errors": 1}
        assert kwargs["issues"][1]["track"] == "Aphex Twin - Xtal"

    async def test_without_run_id_no_audit_write(self, captured_finalize):
        async def coro() -> OperationResult:
            return OperationResult(operation_name="x")

        # No run_id/user_id pair → the seam doesn't touch the audit log.
        await sse_operations.run_sse_operation(_op_id(), coro())

        captured_finalize.assert_not_awaited()


class TestRunSseOperationUncaughtException:
    """The exception path is the seam's own — the result mapping never sees it.

    A use case that raises returns no ``OperationResult``, so status, counts and
    the reason are all constructed here.
    """

    async def test_uncaught_exception_finalizes_error_with_the_message(
        self, captured_finalize
    ):
        async def coro() -> None:
            raise RuntimeError("boom")

        await sse_operations.run_sse_operation(
            _op_id(), coro(), run_id=uuid4(), user_id="u1"
        )

        kwargs = captured_finalize.await_args.kwargs
        assert kwargs["status"] == "error"
        assert kwargs["issues"] == [{"message": "boom"}]

    async def test_message_less_exception_records_its_type(self, captured_finalize):
        # `raise KeyError()` has an empty str() — a blank issue is worse than
        # none, so the type name stands in.
        async def coro() -> None:
            raise KeyError

        await sse_operations.run_sse_operation(
            _op_id(), coro(), run_id=uuid4(), user_id="u1"
        )

        kwargs = captured_finalize.await_args.kwargs
        assert kwargs["issues"] == [{"message": "KeyError"}]


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


class TestRunSseOperationCancellation:
    """A server shutdown (Fly autostop → SIGINT → task.cancel()) must not read as
    success. ``CancelledError`` is a ``BaseException``, so it bypasses the seam's
    ``except Exception`` — before the dedicated branch existed it fell through to
    ``finally`` with the optimistic ``status = "complete"`` default still set, and a
    killed import (15,317 ingested plays, nothing projected) was durably recorded as
    a clean run with empty counts.
    """

    async def test_cancellation_finalizes_error_with_reason_and_reraises(
        self, captured_finalize
    ):
        entered = asyncio.Event()

        async def coro() -> None:
            entered.set()
            await asyncio.Event().wait()  # never resolves; only cancellation ends it

        task = asyncio.create_task(
            sse_operations.run_sse_operation(
                _op_id(), coro(), run_id=uuid4(), user_id="u1"
            )
        )
        await entered.wait()
        task.cancel()

        # Cancellation semantics are preserved: the task ends cancelled, it does not
        # quietly return as if the operation had finished.
        with pytest.raises(asyncio.CancelledError):
            await task

        kwargs = captured_finalize.await_args.kwargs
        assert kwargs["status"] == "error"
        assert kwargs["counts"] == {
            "error_message": sse_operations.CANCELLED_ERROR_MESSAGE
        }
        assert kwargs["issues"] == [{"message": sse_operations.CANCELLED_ISSUE_MESSAGE}]

    async def test_cancellation_pushes_terminal_error_event(self, captured_finalize):
        registry = get_operation_registry()
        op_id = _op_id()
        queue = await registry.register(op_id)
        try:
            entered = asyncio.Event()

            async def coro() -> None:
                entered.set()
                await asyncio.Event().wait()

            task = asyncio.create_task(
                sse_operations.run_sse_operation(
                    op_id, coro(), run_id=uuid4(), user_id="u1"
                )
            )
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            terminal = [
                e for e in _drain(queue) if e.get("event") in ("complete", "error")
            ]
            assert len(terminal) == 1
            assert terminal[0]["event"] == "error"
            assert (
                terminal[0]["data"]["counts"]["error_message"]
                == sse_operations.CANCELLED_ERROR_MESSAGE
            )
        finally:
            await registry.unregister(op_id)

    async def test_cancellation_skips_the_sse_grace_period(self, captured_finalize):
        # The 30s read window exists for a live client; holding it per task would
        # blow the shutdown drain's kill_timeout budget.
        entered = asyncio.Event()

        async def coro() -> None:
            entered.set()
            await asyncio.Event().wait()

        with patch.object(
            sse_operations, "finalize_sse_operation", new=AsyncMock()
        ) as finalize_sse:
            task = asyncio.create_task(
                sse_operations.run_sse_operation(
                    _op_id(), coro(), run_id=uuid4(), user_id="u1"
                )
            )
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert finalize_sse.await_args.kwargs["grace_period_seconds"] == 0.0

    async def test_clean_run_keeps_the_default_grace_period(self, captured_finalize):
        async def coro() -> OperationResult:
            return OperationResult(operation_name="Import")

        with patch.object(
            sse_operations, "finalize_sse_operation", new=AsyncMock()
        ) as finalize_sse:
            await sse_operations.run_sse_operation(
                _op_id(), coro(), run_id=uuid4(), user_id="u1"
            )

        assert finalize_sse.await_args.kwargs["grace_period_seconds"] is None

    async def test_audit_write_lands_despite_cancellation_mid_write(self):
        """The shield is the whole point: the finalize write must survive.

        A second cancellation arriving while the audit write is in flight — the
        realistic shutdown shape, where the drain and the ASGI cancel scope both
        push — would interrupt an unshielded ``finalize_run`` at its first
        suspension point, leaving the run row untouched forever.
        """
        write_started = asyncio.Event()
        recorded: list[str] = []

        async def slow_finalize(
            _run_id, *, user_id: str, status: str, counts, issues
        ) -> None:
            write_started.set()
            await asyncio.sleep(0.05)  # the suspension point cancellation would hit
            recorded.append(status)

        entered = asyncio.Event()

        async def coro() -> None:
            entered.set()
            await asyncio.Event().wait()

        with (
            patch.object(sse_operations, "finalize_run", new=slow_finalize),
            patch.object(sse_operations, "finalize_sse_operation", new=AsyncMock()),
        ):
            task = asyncio.create_task(
                sse_operations.run_sse_operation(
                    _op_id(), coro(), run_id=uuid4(), user_id="u1"
                )
            )
            await entered.wait()
            task.cancel()
            await write_started.wait()
            task.cancel()  # lands while the shielded write is suspended
            with pytest.raises(asyncio.CancelledError):
                await task

        assert recorded == ["error"]

    async def test_audit_write_failure_does_not_break_cleanup(self, captured_finalize):
        # A failed audit write must not swallow the terminal event or the
        # cancellation — the live client still needs to be told.
        captured_finalize.side_effect = RuntimeError("db gone")
        registry = get_operation_registry()
        op_id = _op_id()
        queue = await registry.register(op_id)
        try:
            entered = asyncio.Event()

            async def coro() -> None:
                entered.set()
                await asyncio.Event().wait()

            task = asyncio.create_task(
                sse_operations.run_sse_operation(
                    op_id, coro(), run_id=uuid4(), user_id="u1"
                )
            )
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            terminal = [e for e in _drain(queue) if e.get("event") == "error"]
            assert len(terminal) == 1
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
