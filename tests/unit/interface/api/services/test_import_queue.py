"""Unit tests for the GDPR import queue sequencer (``import_queue``).

Pins the queue's four load-bearing properties: strictly sequential drain
(entry N+1 launches only on N's terminal callback, and a failed entry does not
stop the loop), the one-slot concurrency invariant (a whole queue charges the
shared cap exactly once, leaving room for unrelated operations), cancel
semantics (pending entries cancelled + unlinked, the running one untouched),
and the startup sweep of orphaned queue temp dirs.

``launch_sse_operation`` is patched on the queue module — the launcher's own
behavior is pinned in ``test_sse_operations.py``; here it is a hand-cranked
seam whose captured ``on_terminal`` callbacks drive the drain deterministically.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException
import pytest

from src.interface.api.schemas.imports import (
    OperationStartedResponse,
    QueueEntryStatus,
)
from src.interface.api.services import import_queue, sse_operations
from src.interface.api.services.import_queue import (
    IMPORT_QUEUE_TMPDIR_PREFIX,
    ImportQueue,
    QueueEntry,
    any_queue_undrained,
    cancel_pending,
    cleanup_orphaned_queue_dirs,
    start_queue,
)


@pytest.fixture(autouse=True)
def _clean_queue_state():
    """The registry and the slot set are module-global; leak nothing across tests."""
    import_queue._queues.clear()
    sse_operations._active_operations.clear()
    yield
    import_queue._queues.clear()
    sse_operations._active_operations.clear()


async def _yield_loop(rounds: int = 10) -> None:
    """Give the drain task enough scheduler turns to reach its next await."""
    for _ in range(rounds):
        await asyncio.sleep(0)


class _CapturedLaunch:
    """Fake ``launch_sse_operation`` recording each call's on_terminal hook."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.terminals: list = []

    async def __call__(self, **kwargs: object) -> OperationStartedResponse:
        self.calls.append(kwargs)
        self.terminals.append(kwargs["on_terminal"])
        return OperationStartedResponse(
            operation_id=f"op-{len(self.calls)}", run_id=str(uuid4())
        )


def _entries(tmp_path: Path, count: int) -> list[QueueEntry]:
    entries = []
    for position in range(count):
        path = tmp_path / f"{position:03d}.json"
        path.write_bytes(b"[]")
        entries.append(
            QueueEntry(filename=f"file-{position}.json", position=position, path=path)
        )
    return entries


class TestQueueDrainOrder:
    async def test_next_entry_launches_only_on_previous_terminal(self, tmp_path):
        launcher = _CapturedLaunch()
        with patch.object(import_queue, "launch_sse_operation", launcher):
            queue = start_queue(
                user_id="u1", tmpdir=tmp_path, entries=_entries(tmp_path, 3)
            )
            await _yield_loop()

            # Only the first entry has launched; the rest wait their turn.
            assert len(launcher.calls) == 1
            assert queue.entries[0].status == "running"
            assert queue.entries[0].operation_id == "op-1"
            assert queue.entries[0].run_id is not None
            assert queue.entries[1].status == "queued"

            launcher.terminals[0]("complete")
            await _yield_loop()
            assert len(launcher.calls) == 2
            assert queue.entries[0].status == "complete"
            assert queue.entries[1].status == "running"

            launcher.terminals[1]("complete")
            await _yield_loop()
            launcher.terminals[2]("complete")
            await _yield_loop()

        assert [e.status for e in queue.entries] == ["complete"] * 3
        assert queue.is_drained

    async def test_failed_entry_does_not_stop_the_queue(self, tmp_path):
        launcher = _CapturedLaunch()
        with patch.object(import_queue, "launch_sse_operation", launcher):
            queue = start_queue(
                user_id="u1", tmpdir=tmp_path, entries=_entries(tmp_path, 3)
            )
            await _yield_loop()
            launcher.terminals[0]("error")
            await _yield_loop()

            # The failure is recorded and the remainder still runs.
            assert queue.entries[0].status == "error"
            assert len(launcher.calls) == 2

            launcher.terminals[1]("partial")
            await _yield_loop()
            launcher.terminals[2]("complete")
            await _yield_loop()

        assert [e.status for e in queue.entries] == ["error", "partial", "complete"]

    async def test_drain_removes_the_queue_tmpdir(self, tmp_path):
        tmpdir = tmp_path / "queue-dir"
        tmpdir.mkdir()
        launcher = _CapturedLaunch()
        with patch.object(import_queue, "launch_sse_operation", launcher):
            _ = start_queue(user_id="u1", tmpdir=tmpdir, entries=_entries(tmpdir, 1))
            await _yield_loop()
            launcher.terminals[0]("complete")
            await _yield_loop()

        assert not tmpdir.exists()

    async def test_launch_failure_marks_entry_error_and_continues(self, tmp_path):
        calls: list[int] = []
        terminals: list = []

        async def flaky_launch(**kwargs: object) -> OperationStartedResponse:
            calls.append(len(calls))
            if len(calls) == 1:
                raise RuntimeError("audit row write failed")
            terminals.append(kwargs["on_terminal"])
            return OperationStartedResponse(operation_id="op", run_id=None)

        entries = _entries(tmp_path, 2)
        with patch.object(import_queue, "launch_sse_operation", flaky_launch):
            queue = start_queue(user_id="u1", tmpdir=tmp_path, entries=entries)
            await _yield_loop()

            assert queue.entries[0].status == "error"
            assert not entries[0].path.exists()

            terminals[0]("complete")
            await _yield_loop()

        assert queue.entries[1].status == "complete"


class TestOneSlotInvariant:
    async def test_whole_queue_holds_exactly_one_slot(self, tmp_path):
        launcher = _CapturedLaunch()
        with patch.object(import_queue, "launch_sse_operation", launcher):
            queue = start_queue(
                user_id="u1", tmpdir=tmp_path, entries=_entries(tmp_path, 13)
            )
            await _yield_loop()

            token = import_queue._queue_slot_token(queue.queue_id)
            # Drive several entries through: the set never grows past the token.
            for index in range(4):
                assert sse_operations._active_operations == {token}
                assert launcher.calls[index]["occupies_slot"] is False
                launcher.terminals[index]("complete")
                await _yield_loop()

            # An unrelated import launched mid-queue is NOT rejected: with the
            # queue token holding one of three slots, the shared 429 check
            # still sees free capacity.
            with (
                patch.object(
                    sse_operations, "start_run", new=AsyncMock(return_value=uuid4())
                ),
                patch.object(sse_operations, "get_progress_broker"),
            ):
                (
                    operation_id,
                    _run_id,
                    _emitter,
                ) = await sse_operations.prepare_sse_operation_with_emitter(
                    user_id="u1", operation_type="import_lastfm_history"
                )
            await sse_operations.get_operation_registry().unregister(operation_id)

            for index in range(4, 13):
                launcher.terminals[index]("complete")
                await _yield_loop()

        # Drain released the queue's own token.
        assert sse_operations._active_operations == set()

    async def test_start_queue_429s_when_every_slot_is_held(self, tmp_path):
        for taken in range(sse_operations.SSEConstants.MAX_CONCURRENT_OPERATIONS):
            sse_operations._active_operations.add(f"other-{taken}")

        with pytest.raises(HTTPException) as exc_info:
            start_queue(user_id="u1", tmpdir=tmp_path, entries=_entries(tmp_path, 1))

        assert exc_info.value.status_code == 429
        # A rejected queue is not registered — the user can retry immediately.
        assert import_queue.get_queue("u1") is None


class TestQueueRegistry:
    async def test_active_predecessor_makes_start_queue_409(self, tmp_path):
        launcher = _CapturedLaunch()
        with patch.object(import_queue, "launch_sse_operation", launcher):
            _ = start_queue(
                user_id="u1", tmpdir=tmp_path, entries=_entries(tmp_path, 1)
            )
            await _yield_loop()

            with pytest.raises(HTTPException) as exc_info:
                start_queue(user_id="u1", tmpdir=tmp_path, entries=[])
            assert exc_info.value.status_code == 409

            launcher.terminals[0]("complete")
            await _yield_loop()

    async def test_drained_predecessor_is_replaced(self, tmp_path):
        drained = ImportQueue(
            queue_id="old",
            user_id="u1",
            tmpdir=tmp_path,
            entries=[
                QueueEntry(
                    filename="a.json",
                    position=0,
                    path=tmp_path / "000.json",
                    status="complete",
                )
            ],
        )
        import_queue._queues["u1"] = drained

        launcher = _CapturedLaunch()
        with patch.object(import_queue, "launch_sse_operation", launcher):
            replacement = start_queue(
                user_id="u1", tmpdir=tmp_path, entries=_entries(tmp_path, 1)
            )
            await _yield_loop()
            assert import_queue.get_queue("u1") is replacement
            launcher.terminals[0]("complete")
            await _yield_loop()


class TestAnyQueueUndrained:
    """The busy gate's in-process half: queued files have no run row yet."""

    def _queue_with_status(
        self, tmp_path: Path, status: QueueEntryStatus
    ) -> ImportQueue:
        return ImportQueue(
            queue_id="q1",
            user_id="u1",
            tmpdir=tmp_path,
            entries=[
                QueueEntry(
                    filename="a.json",
                    position=0,
                    path=tmp_path / "000.json",
                    status=status,
                )
            ],
        )

    def test_empty_registry_is_not_busy(self):
        assert any_queue_undrained() is False

    def test_queued_not_started_entry_is_busy(self, tmp_path):
        # The critical case: no operation_runs row exists for this entry, so
        # only this in-process check can stop a deploy landing mid-queue.
        import_queue._queues["u1"] = self._queue_with_status(tmp_path, "queued")
        assert any_queue_undrained() is True

    def test_drained_queue_is_not_busy(self, tmp_path):
        import_queue._queues["u1"] = self._queue_with_status(tmp_path, "complete")
        assert any_queue_undrained() is False


class TestCancelPending:
    async def test_cancels_and_unlinks_only_unstarted_entries(self, tmp_path):
        entries = _entries(tmp_path, 3)
        entries[0].status = "running"
        import_queue._queues["u1"] = ImportQueue(
            queue_id="q", user_id="u1", tmpdir=tmp_path, entries=entries
        )

        queue = cancel_pending("u1")

        assert queue is not None
        assert [e.status for e in queue.entries] == [
            "running",
            "cancelled",
            "cancelled",
        ]
        assert entries[0].path.exists()
        assert not entries[1].path.exists()
        assert not entries[2].path.exists()

    def test_no_queue_returns_none(self):
        assert cancel_pending("nobody") is None


class TestCleanupOrphanedQueueDirs:
    def test_removes_only_prefix_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(import_queue.tempfile, "gettempdir", lambda: str(tmp_path))
        orphan = tmp_path / f"{IMPORT_QUEUE_TMPDIR_PREFIX}abc123"
        orphan.mkdir()
        (orphan / "000.json").write_bytes(b"[]")
        unrelated = tmp_path / "unrelated-dir"
        unrelated.mkdir()
        prefix_file = tmp_path / f"{IMPORT_QUEUE_TMPDIR_PREFIX}not-a-dir"
        prefix_file.write_bytes(b"")

        cleanup_orphaned_queue_dirs()

        assert not orphan.exists()
        assert unrelated.exists()
        # Only directories are swept — a prefix-named file is not ours to touch.
        assert prefix_file.exists()

    def test_registered_queue_dir_survives_the_sweep(self, tmp_path, monkeypatch):
        # The sweep runs as an un-awaited startup task while the server already
        # serves, so an early request CAN have registered a queue before it
        # fires — rmtree'ing that dir would destroy a live queue's files.
        monkeypatch.setattr(import_queue.tempfile, "gettempdir", lambda: str(tmp_path))
        live = tmp_path / f"{IMPORT_QUEUE_TMPDIR_PREFIX}live"
        live.mkdir()
        (live / "000.json").write_bytes(b"[]")
        orphan = tmp_path / f"{IMPORT_QUEUE_TMPDIR_PREFIX}orphan"
        orphan.mkdir()
        import_queue._queues["u1"] = ImportQueue(
            queue_id="q1",
            user_id="u1",
            tmpdir=live,
            entries=[QueueEntry(filename="a.json", position=0, path=live / "000.json")],
        )

        cleanup_orphaned_queue_dirs()

        assert live.exists()
        assert (live / "000.json").exists()
        assert not orphan.exists()
