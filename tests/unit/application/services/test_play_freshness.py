"""Unit tests for demand-pulled play freshness.

The coalescing behaviour is the point of this module, so it is asserted directly:
a burst of reads must produce ONE poll, not one per read. Everything else here
guards the ways a fire-and-forget task can go wrong quietly — being garbage
collected mid-flight, or being cancelled out from under a second waiter.
"""

import asyncio

import pytest

from src.application.services import play_freshness
from src.application.services.play_freshness import (
    reset_play_refresh_flight,
    spawn_ensure_fresh_plays,
    wait_for_fresh_plays,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_flight():
    reset_play_refresh_flight()
    yield
    reset_play_refresh_flight()


class _Recorder:
    """Stands in for ``ensure_fresh_plays``, counting and pacing calls."""

    def __init__(self, *, gate: asyncio.Event | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._gate = gate

    async def __call__(
        self, user_id: str, *, trigger_detail: str, max_age=None
    ) -> None:
        self.calls.append((user_id, trigger_detail))
        if self._gate is not None:
            await self._gate.wait()


class TestCoalescing:
    async def test_a_burst_for_one_user_produces_one_poll(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The reason the in-flight map exists.

        A single page load hits the dashboard and several track routes at once.
        Without coalescing that is four tasks and four round-trips against a
        database we are trying to let sleep — three of which the poll lease would
        discard anyway.
        """
        gate = asyncio.Event()
        recorder = _Recorder(gate=gate)
        monkeypatch.setattr(play_freshness, "ensure_fresh_plays", recorder)

        tasks = [spawn_ensure_fresh_plays("u1", trigger_detail="web") for _ in range(4)]
        await asyncio.sleep(0)

        assert len(recorder.calls) == 1
        # Every caller joined the SAME task, so none of them is orphaned.
        assert len({id(t) for t in tasks}) == 1

        gate.set()
        await asyncio.gather(*tasks)

    async def test_different_users_do_not_share_a_poll(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gate = asyncio.Event()
        recorder = _Recorder(gate=gate)
        monkeypatch.setattr(play_freshness, "ensure_fresh_plays", recorder)

        t1 = spawn_ensure_fresh_plays("u1", trigger_detail="web")
        t2 = spawn_ensure_fresh_plays("u2", trigger_detail="web")
        await asyncio.sleep(0)

        assert len(recorder.calls) == 2
        assert t1 is not t2

        gate.set()
        await asyncio.gather(t1, t2)

    async def test_a_finished_refresh_does_not_block_the_next_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _Recorder()
        monkeypatch.setattr(play_freshness, "ensure_fresh_plays", recorder)

        first = spawn_ensure_fresh_plays("u1", trigger_detail="web")
        assert first is not None
        await first
        # Past the throttle window, a finished refresh is replaceable.
        reset_play_refresh_flight()
        second = spawn_ensure_fresh_plays("u1", trigger_detail="web")
        assert second is not None
        await second

        assert len(recorder.calls) == 2

    async def test_the_map_empties_when_a_refresh_completes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A map that only grew would be a slow leak on a long-lived process. A
        # fresh task for the same key proves the finished one was evicted.
        recorder = _Recorder()
        monkeypatch.setattr(play_freshness, "ensure_fresh_plays", recorder)

        first = spawn_ensure_fresh_plays("u1", trigger_detail="web")
        assert first is not None
        await first
        await asyncio.sleep(0)

        # Within the throttle window a second trigger is declined outright,
        # which is the point: no task, and no transaction to ask about one.
        assert spawn_ensure_fresh_plays("u1", trigger_detail="web") is None

    async def test_a_failing_refresh_is_swallowed_and_forgotten(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _boom(user_id: str, *, trigger_detail: str, max_age=None) -> None:
            raise RuntimeError("spotify down")

        monkeypatch.setattr(play_freshness, "ensure_fresh_plays", _boom)

        task = spawn_ensure_fresh_plays("u1", trigger_detail="web")
        assert task is not None
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # The read this served has already returned; a background failure must
        # not surface as an unretrieved-exception warning or block a retry.
        assert task.done()
        reset_play_refresh_flight()
        assert spawn_ensure_fresh_plays("u1", trigger_detail="web") is not task


class TestBoundedWait:
    async def test_returns_true_when_the_refresh_finishes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(play_freshness, "ensure_fresh_plays", _Recorder())
        assert await wait_for_fresh_plays("u1", trigger_detail="workflow:1") is True

    async def test_proceeds_on_timeout_without_cancelling_the_task(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A slow connector must delay a workflow, not stall or corrupt it.

        The pending task is deliberately left running: it may be shared with
        other waiters, and cancelling mid-import would abandon a claimed poll
        lease and leave a half-written checkpoint for the TTL to clean up.
        """
        gate = asyncio.Event()
        monkeypatch.setattr(play_freshness, "ensure_fresh_plays", _Recorder(gate=gate))
        running = spawn_ensure_fresh_plays("u1", trigger_detail="web")
        assert running is not None

        finished = await wait_for_fresh_plays(
            "u1", trigger_detail="workflow:1", timeout_seconds=0.01
        )

        assert finished is False
        # Still running: cancelling would abandon a claimed lease mid-import.
        assert not running.done()

        gate.set()
        await running

    async def test_a_waiter_joins_an_already_running_refresh(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gate = asyncio.Event()
        recorder = _Recorder(gate=gate)
        monkeypatch.setattr(play_freshness, "ensure_fresh_plays", recorder)

        spawned = spawn_ensure_fresh_plays("u1", trigger_detail="web")
        assert spawned is not None
        await asyncio.sleep(0)
        gate.set()

        assert await wait_for_fresh_plays("u1", trigger_detail="workflow:1") is True
        # The workflow waited on the web trigger's poll rather than starting a
        # second one against the same window.
        assert len(recorder.calls) == 1
        assert spawned.done()


class TestAttemptThrottle:
    """The pre-check that keeps declined refreshes off the database entirely.

    Coalescing collapses only *concurrent* triggers. Sequential ones — someone
    clicking through track pages — each opened a token read and a two-SELECT
    transaction just to be told "polled 30 seconds ago", which is the opposite of
    letting a scale-to-zero database sleep.
    """

    async def test_a_second_sequential_trigger_is_declined(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _Recorder()
        monkeypatch.setattr(play_freshness, "ensure_fresh_plays", recorder)

        first = spawn_ensure_fresh_plays("u1", trigger_detail="web")
        assert first is not None
        await first

        assert spawn_ensure_fresh_plays("u1", trigger_detail="web") is None
        assert len(recorder.calls) == 1

    async def test_the_throttle_is_per_user(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _Recorder()
        monkeypatch.setattr(play_freshness, "ensure_fresh_plays", recorder)

        first = spawn_ensure_fresh_plays("u1", trigger_detail="web")
        assert first is not None
        await first
        second = spawn_ensure_fresh_plays("u2", trigger_detail="web")
        assert second is not None
        await second

        assert len(recorder.calls) == 2

    async def test_a_waiter_joins_an_in_flight_poll_inside_the_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Join must beat the throttle, or a workflow runs on half-fetched data.

        If the throttle were checked first, `wait_for_fresh_plays` would be told
        "recent enough" and proceed while the poll it should have awaited was
        still running.
        """
        gate = asyncio.Event()
        recorder = _Recorder(gate=gate)
        monkeypatch.setattr(play_freshness, "ensure_fresh_plays", recorder)

        running = spawn_ensure_fresh_plays("u1", trigger_detail="web")
        assert running is not None
        await asyncio.sleep(0)

        joined = spawn_ensure_fresh_plays("u1", trigger_detail="workflow:1")
        assert joined is running

        gate.set()
        await running

    async def test_a_throttled_waiter_proceeds_immediately(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Nothing in flight and a refresh a moment ago: the data is already as
        # fresh as polling now would make it, so the workflow must not block.
        recorder = _Recorder()
        monkeypatch.setattr(play_freshness, "ensure_fresh_plays", recorder)

        first = spawn_ensure_fresh_plays("u1", trigger_detail="web")
        assert first is not None
        await first

        assert await wait_for_fresh_plays("u1", trigger_detail="workflow:1") is True
        assert len(recorder.calls) == 1
