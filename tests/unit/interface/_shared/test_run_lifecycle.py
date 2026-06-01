"""Unit tests for the shared run-lifecycle heartbeat ticker.

The heartbeat runs as an asyncio task. CPU-bound transform/combiner nodes are
offloaded to a worker thread (see ``node_factories``), so the event loop stays
responsive and this ticker keeps firing even under heavy transforms. These tests
cover the loop's tick cadence, its cancellation exit path, and the advisory
error-suppression of a single bump. The DB write is mocked throughout.

These helpers are shared by the CLI and API (``src/interface/_shared/
run_lifecycle.py``) so the run lifecycle lives in one place.
"""

import asyncio
import contextlib
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import src.interface._shared.run_lifecycle as rl


class TestHeartbeatLoop:
    async def test_bumps_immediately_then_on_each_interval(self) -> None:
        """The loop bumps once at once, then again every interval, until cancelled."""
        run_id = uuid4()
        calls: list[object] = []

        async def record(rid: object) -> None:
            calls.append(rid)

        with patch.object(rl, "bump_heartbeat", AsyncMock(side_effect=record)):
            task = asyncio.create_task(rl.heartbeat_loop(run_id, interval_seconds=0.01))
            await asyncio.sleep(0.05)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        # Immediate first bump + several interval ticks during the wait.
        assert len(calls) >= 2
        assert all(rid == run_id for rid in calls)

    async def test_cancellation_stops_the_loop(self) -> None:
        """Cancelling the task (run reached a terminal state) halts further bumps."""
        run_id = uuid4()
        calls: list[object] = []

        async def record(rid: object) -> None:
            calls.append(rid)

        with patch.object(rl, "bump_heartbeat", AsyncMock(side_effect=record)):
            task = asyncio.create_task(rl.heartbeat_loop(run_id, interval_seconds=0.01))
            await asyncio.sleep(0.03)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

            ticks_after_stop = len(calls)
            await asyncio.sleep(0.03)
            # No further bumps once cancelled.
            assert len(calls) == ticks_after_stop


class TestBumpHeartbeat:
    async def test_suppresses_errors(self) -> None:
        """Heartbeats are advisory — a DB blip during a bump must not propagate."""
        run_id = uuid4()

        with patch.object(rl, "run_repo_session", side_effect=RuntimeError("db down")):
            await rl.bump_heartbeat(run_id)  # must not raise
