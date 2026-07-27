"""Unit tests for the in-process run-activity signal.

This gates the stalled-run sweeper's cadence, so its failure modes are cost
failures rather than correctness ones: a count stuck above zero pins the sweeper
at its 30s active rhythm and re-defeats Neon's scale-to-zero, while a lost wake
signal leaves a starting run unswept until the long idle fallback expires.
"""

import asyncio

import pytest

from src.application.services.run_activity import (
    activity_event,
    reset_run_activity,
    runs_in_flight,
    track_run,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _fresh_signal():
    """Each test gets its own counter and event (and its own event loop)."""
    reset_run_activity()
    yield
    reset_run_activity()


class TestTracking:
    async def test_counts_a_run_for_the_duration_of_the_block(self) -> None:
        assert runs_in_flight() == 0
        async with track_run():
            assert runs_in_flight() == 1
        assert runs_in_flight() == 0

    async def test_counts_concurrent_runs(self) -> None:
        async with track_run():
            async with track_run():
                assert runs_in_flight() == 2
            assert runs_in_flight() == 1
        assert runs_in_flight() == 0

    async def test_release_survives_an_exception(self) -> None:
        # A crashing run must not leave the count raised — that would hold the
        # sweeper at its active cadence forever.
        with pytest.raises(ValueError):
            async with track_run():
                raise ValueError("boom")
        assert runs_in_flight() == 0

    async def test_release_survives_cancellation(self) -> None:
        started = asyncio.Event()

        async def _run() -> None:
            async with track_run():
                started.set()
                await asyncio.sleep(3600)

        task = asyncio.create_task(_run())
        await started.wait()
        assert runs_in_flight() == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert runs_in_flight() == 0

    async def test_count_never_goes_negative(self) -> None:
        # Defensive clamp: an unbalanced release must not drive the count below
        # zero, which would strand the sweeper at the active cadence once a real
        # run later decremented past it.
        async with track_run():
            pass
        assert runs_in_flight() == 0


class TestWakeSignal:
    async def test_starting_a_run_sets_the_event(self) -> None:
        assert not activity_event().is_set()
        async with track_run():
            assert activity_event().is_set()

    async def test_event_stays_set_until_the_consumer_clears_it(self) -> None:
        # The sweeper loop owns the clear (it clears before each tick), so the
        # signal must survive the run finishing — otherwise a short run that
        # starts and ends inside one sleep would never wake the sweeper.
        async with track_run():
            pass
        assert activity_event().is_set()
