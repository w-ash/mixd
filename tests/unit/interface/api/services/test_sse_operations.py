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

from fastapi import HTTPException
import pytest

from src.config.constants import SSEConstants
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


class _TerminalRecorder:
    """Captures every ``on_terminal`` call as ``(status, counts)``."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    def __call__(self, status: str, counts: dict[str, object] | None) -> None:
        self.calls.append((status, counts))

    @property
    def statuses(self) -> list[str]:
        return [status for status, _counts in self.calls]


class TestOnTerminalCallback:
    """``on_terminal`` is the sequencer's advance signal: it must fire with the
    final status AND the run's counts on every path — clean, soft failure,
    uncaught exception, and cancellation — and before the SSE grace period, or
    a queued export would wait out 30s of grace per file.

    The counts are what let a sequencer keep a finished item's real numbers:
    they arrive on that run's own stream, which closes moments later, so a
    parent surface has no other chance to read them."""

    async def test_fires_with_complete_on_clean_result(self, captured_finalize):
        seen = _TerminalRecorder()

        async def coro() -> OperationResult:
            return OperationResult(operation_name="Import")

        await sse_operations.run_sse_operation(
            _op_id(), coro(), run_id=uuid4(), user_id="u1", on_terminal=seen
        )

        assert seen.statuses == ["complete"]

    async def test_fires_with_error_on_soft_failure(self, captured_finalize):
        seen = _TerminalRecorder()
        result = OperationResult(operation_name="Import")
        result.summary_metrics.add("errors", 1, "Errors", significance=1)
        result.metadata["error"] = "bad file"

        async def coro() -> OperationResult:
            return result

        await sse_operations.run_sse_operation(
            _op_id(), coro(), run_id=uuid4(), user_id="u1", on_terminal=seen
        )

        assert seen.statuses == ["error"]
        # The counts ride along, not just the verdict.
        assert (seen.calls[0][1] or {}).get("errors") == 1

    async def test_fires_with_error_on_uncaught_exception(self, captured_finalize):
        seen = _TerminalRecorder()

        async def coro() -> None:
            raise RuntimeError("boom")

        await sse_operations.run_sse_operation(
            _op_id(), coro(), run_id=uuid4(), user_id="u1", on_terminal=seen
        )

        assert seen.statuses == ["error"]
        assert (seen.calls[0][1] or {}).get("error_message") == "boom"

    async def test_fires_on_cancellation_and_cancellation_still_propagates(
        self, captured_finalize
    ):
        seen = _TerminalRecorder()
        entered = asyncio.Event()

        async def coro() -> None:
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            sse_operations.run_sse_operation(
                _op_id(), coro(), run_id=uuid4(), user_id="u1", on_terminal=seen
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert seen.statuses == ["error"]

    async def test_fires_before_the_sse_grace_period(self):
        # The sequencer must be able to start the next entry the moment this
        # one settles — not after finalize_sse_operation's 30s read window.
        order: list[str] = []

        async def fake_finalize_sse(
            _operation_id: str, *, grace_period_seconds: float | None = None
        ) -> None:
            order.append("grace")

        async def coro() -> OperationResult:
            return OperationResult(operation_name="Import")

        with (
            patch.object(sse_operations, "finalize_run", new=AsyncMock()),
            patch.object(
                sse_operations, "finalize_sse_operation", new=fake_finalize_sse
            ),
        ):
            await sse_operations.run_sse_operation(
                _op_id(),
                coro(),
                run_id=uuid4(),
                user_id="u1",
                on_terminal=lambda _status, _counts: order.append("terminal"),
            )

        assert order == ["terminal", "grace"]

    async def test_callback_failure_does_not_break_teardown(self, captured_finalize):
        def explode(_status: str, _counts: dict[str, object] | None) -> None:
            raise RuntimeError("observer broke")

        async def coro() -> OperationResult:
            return OperationResult(operation_name="Import")

        # Must not raise: the callback is an observer, not a lifecycle owner.
        await sse_operations.run_sse_operation(
            _op_id(), coro(), run_id=uuid4(), user_id="u1", on_terminal=explode
        )


class TestOccupiesSlot:
    """``occupies_slot=False`` keeps a run out of ``_active_operations``: the
    caller's own token (the import queue's) already charges the shared cap."""

    async def test_false_never_touches_the_active_set(self, captured_finalize):
        op_id = _op_id()
        sampled: list[bool] = []

        async def coro() -> OperationResult:
            sampled.append(op_id in sse_operations._active_operations)
            return OperationResult(operation_name="Import")

        await sse_operations.run_sse_operation(
            op_id, coro(), run_id=uuid4(), user_id="u1", occupies_slot=False
        )

        assert sampled == [False]
        assert op_id not in sse_operations._active_operations

    async def test_default_still_brackets_the_run_with_the_slot(
        self, captured_finalize
    ):
        op_id = _op_id()
        sampled: list[bool] = []

        async def coro() -> OperationResult:
            sampled.append(op_id in sse_operations._active_operations)
            return OperationResult(operation_name="Import")

        await sse_operations.run_sse_operation(
            op_id, coro(), run_id=uuid4(), user_id="u1"
        )

        assert sampled == [True]
        assert op_id not in sse_operations._active_operations

    async def test_prepare_skips_the_429_check_when_not_occupying(self):
        taken = [f"held-{n}" for n in range(SSEConstants.MAX_CONCURRENT_OPERATIONS)]
        sse_operations._active_operations.update(taken)
        try:
            with (
                patch.object(
                    sse_operations, "start_run", new=AsyncMock(return_value=uuid4())
                ),
                patch.object(sse_operations, "get_progress_broker", new=MagicMock()),
            ):
                # Default path: full set → 429.
                with pytest.raises(HTTPException) as exc_info:
                    await sse_operations.prepare_sse_operation_with_emitter(
                        user_id="u1", operation_type="import_spotify_history"
                    )
                assert exc_info.value.status_code == 429

                # Slot-exempt path: same full set, no rejection.
                (
                    operation_id,
                    _run_id,
                    _emitter,
                ) = await sse_operations.prepare_sse_operation_with_emitter(
                    user_id="u1",
                    operation_type="import_spotify_history",
                    occupies_slot=False,
                )
            await get_operation_registry().unregister(operation_id)
        finally:
            for token in taken:
                sse_operations._active_operations.discard(token)


class TestOperationSlotPair:
    """acquire/release are the durable-claim primitives the queue holds its
    drain-long token through; the 429 they raise is the same one prepare uses."""

    def test_acquire_adds_and_release_removes(self):
        sse_operations.acquire_operation_slot("queue_x")
        assert "queue_x" in sse_operations._active_operations
        sse_operations.release_operation_slot("queue_x")
        assert "queue_x" not in sse_operations._active_operations

    def test_acquire_raises_429_at_capacity(self):
        taken = [f"held-{n}" for n in range(SSEConstants.MAX_CONCURRENT_OPERATIONS)]
        sse_operations._active_operations.update(taken)
        try:
            with pytest.raises(HTTPException) as exc_info:
                sse_operations.acquire_operation_slot("one-too-many")
            assert exc_info.value.status_code == 429
            assert "one-too-many" not in sse_operations._active_operations
        finally:
            for token in taken:
                sse_operations._active_operations.discard(token)


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
            occupies_slot: bool = True,
            parent_operation_id: str | None = None,
            on_terminal: object = None,
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
