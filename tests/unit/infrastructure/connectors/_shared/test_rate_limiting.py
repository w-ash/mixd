"""Unit tests for ConnectorRateLimiter and the per-service limiter registry.

Pacing is driven by an injected clock + sleep, so nothing here waits on wall
time or asserts against it.
"""

import asyncio
from collections.abc import Iterator

from hypothesis import given, settings as hypothesis_settings, strategies as st
import pytest

from src.config import settings
from src.infrastructure.connectors._shared import rate_limiting
from src.infrastructure.connectors._shared.rate_limiting import (
    ConnectorRateLimiter,
    get_connector_rate_limiter,
)


class FakeTime:
    """Injectable clock whose sleep advances the clock instead of waiting."""

    def __init__(self) -> None:
        self.now: float = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay

    async def yielding_sleep(self, delay: float) -> None:
        """Sleep that also hands control to the loop, without wall-clock delay."""
        self.sleeps.append(delay)
        self.now += delay
        await asyncio.sleep(0)


def _make_limiter(rate: float, clock: FakeTime) -> ConnectorRateLimiter:
    return ConnectorRateLimiter(
        rate_per_second=rate, clock=clock.monotonic, sleep=clock.sleep
    )


class TestBurstAndPacing:
    """Bucket starts full, drains over the burst, then paces exactly."""

    async def test_first_acquire_does_not_wait(self):
        clock = FakeTime()
        limiter = _make_limiter(5.0, clock)

        await limiter.acquire()

        assert clock.sleeps == []
        assert clock.now == 0.0

    async def test_burst_of_capacity_drains_without_waiting(self):
        clock = FakeTime()
        limiter = _make_limiter(5.0, clock)

        for _ in range(5):
            await limiter.acquire()

        assert clock.sleeps == []

    async def test_acquires_past_the_burst_wait_one_token_interval(self):
        clock = FakeTime()
        limiter = _make_limiter(5.0, clock)

        for _ in range(8):
            await limiter.acquire()

        # 5 free (capacity = 1s of tokens), then 1/5s each.
        assert clock.sleeps == pytest.approx([0.2, 0.2, 0.2])

    async def test_wait_is_exactly_the_token_deficit(self):
        clock = FakeTime()
        limiter = _make_limiter(5.0, clock)

        for _ in range(5):
            await limiter.acquire()
        clock.now += 0.1  # refills 0.5 tokens

        await limiter.acquire()

        # 0.5 tokens short of 1.0 at 5/s = 0.1s.
        assert clock.sleeps == pytest.approx([0.1])

    async def test_sub_unit_rate_still_admits_one_call_immediately(self):
        clock = FakeTime()
        limiter = _make_limiter(0.5, clock)

        await limiter.acquire()
        await limiter.acquire()

        assert clock.sleeps == pytest.approx([2.0])

    async def test_context_manager_acquires_on_entry(self):
        clock = FakeTime()
        limiter = _make_limiter(1.0, clock)

        async with limiter:
            pass
        async with limiter:
            pass

        assert clock.sleeps == pytest.approx([1.0])

    def test_non_positive_rate_is_rejected(self):
        with pytest.raises(ValueError, match="rate_per_second must be positive"):
            _ = ConnectorRateLimiter(rate_per_second=0.0)


class TestPacingProperties:
    """Property-based sanity: waits are non-negative and cover the deficit."""

    @hypothesis_settings(deadline=None, max_examples=50)
    @given(
        rate=st.floats(min_value=0.1, max_value=50.0),
        count=st.integers(min_value=1, max_value=40),
    )
    def test_total_wait_covers_the_deficit_beyond_the_burst(
        self, rate: float, count: int
    ):
        clock = FakeTime()

        async def drain() -> None:
            limiter = _make_limiter(rate, clock)
            for _ in range(count):
                await limiter.acquire()

        asyncio.run(drain())

        burst = max(1.0, rate)
        expected_minimum = max(0.0, (count - burst) / rate)
        assert all(delay >= 0.0 for delay in clock.sleeps)
        assert sum(clock.sleeps) >= expected_minimum - 1e-9


class TestConcurrentAcquirers:
    """Bookkeeping stays consistent when many coroutines acquire at once."""

    async def test_concurrent_acquirers_do_not_over_issue_tokens(self):
        clock = FakeTime()
        limiter = ConnectorRateLimiter(
            rate_per_second=10.0, clock=clock.monotonic, sleep=clock.yielding_sleep
        )
        issued_at: list[float] = []

        async def acquire_one() -> None:
            await limiter.acquire()
            issued_at.append(clock.now)

        async with asyncio.TaskGroup() as tg:
            for _ in range(20):
                _ = tg.create_task(acquire_one())

        # Capacity is 10 tokens; only those may be issued at t=0, the rest pace
        # at 1/10s each and no acquirer skips its wait.
        assert sum(1 for at in issued_at if at == 0.0) == 10
        assert clock.sleeps == pytest.approx([0.1] * 10)
        assert sorted(issued_at) == pytest.approx(
            [0.0] * 10 + [round(0.1 * (i + 1), 10) for i in range(10)]
        )


class TestPauseFor:
    """A server-declared 429 window brakes every acquirer, not just the caller."""

    async def test_pause_shifts_the_next_acquire_by_the_pause(self):
        clock = FakeTime()
        limiter = _make_limiter(5.0, clock)

        limiter.pause_for(5.0)
        await limiter.acquire()

        # Bucket was full — without the pause this acquire would not have slept.
        # Serving the pause empties the bucket, so the acquire that follows it
        # also pays one token interval instead of riding the accrued burst.
        assert clock.sleeps == pytest.approx([5.0, 0.2])
        assert clock.now == pytest.approx(5.2)

    async def test_expired_pause_does_not_delay(self):
        clock = FakeTime()
        limiter = _make_limiter(5.0, clock)

        limiter.pause_for(2.0)
        clock.now = 5.0
        await limiter.acquire()

        assert clock.sleeps == []

    async def test_pause_only_extends_never_shortens(self):
        clock = FakeTime()
        limiter = _make_limiter(5.0, clock)

        limiter.pause_for(5.0)
        limiter.pause_for(1.0)
        await limiter.acquire()

        # The longer window is served once; the trailing 0.2 is the post-pause
        # token interval, not a second pause.
        assert clock.sleeps == pytest.approx([5.0, 0.2])

    def test_non_positive_pause_is_a_no_op(self):
        clock = FakeTime()
        limiter = _make_limiter(5.0, clock)

        limiter.pause_for(0.0)
        limiter.pause_for(-3.0)

        assert limiter._paused_until == 0.0

    async def test_every_concurrent_acquirer_waits_out_the_window(self):
        """The point of the brake: the other 49 in-flight calls stop spending too."""
        clock = FakeTime()
        limiter = ConnectorRateLimiter(
            rate_per_second=10.0, clock=clock.monotonic, sleep=clock.yielding_sleep
        )
        issued_at: list[float] = []

        async def acquire_one() -> None:
            await limiter.acquire()
            issued_at.append(clock.now)

        limiter.pause_for(3.0)
        async with asyncio.TaskGroup() as tg:
            for _ in range(4):
                _ = tg.create_task(acquire_one())

        # One acquirer sleeps the remainder; the rest re-read the deadline and
        # fall through, so none is issued inside the window. Past it the bucket
        # is empty, so all four emerge serialized at 1/rate — the window does
        # not end in a `capacity`-wide burst.
        assert clock.sleeps == pytest.approx([3.0, 0.1, 0.1, 0.1, 0.1])
        assert sorted(issued_at) == pytest.approx([3.1, 3.2, 3.3, 3.4])

    async def test_resume_after_a_pause_is_paced_not_bursted(self):
        clock = FakeTime()
        limiter = _make_limiter(5.0, clock)

        limiter.pause_for(1.0)
        for _ in range(3):
            await limiter.acquire()

        # Capacity is 5 tokens and 1s of pause would accrue all of them; the
        # bucket is emptied instead, so every acquire pays the 1/rate interval.
        assert clock.sleeps == pytest.approx([1.0, 0.2, 0.2, 0.2])
        assert limiter._tokens == pytest.approx(0.0)

    async def test_pause_landing_during_the_token_wait_is_honored(self):
        """A 429 that arrives mid-token-sleep must not be spent through."""
        clock = FakeTime()
        limiter: ConnectorRateLimiter | None = None
        pause_armed = False

        async def sleep_then_maybe_pause(delay: float) -> None:
            nonlocal pause_armed
            if pause_armed:
                pause_armed = False
                assert limiter is not None
                limiter.pause_for(2.0)
            await clock.sleep(delay)

        limiter = ConnectorRateLimiter(
            rate_per_second=5.0, clock=clock.monotonic, sleep=sleep_then_maybe_pause
        )
        for _ in range(5):  # drain the burst so the next acquire waits for a token
            await limiter.acquire()

        pause_armed = True
        await limiter.acquire()

        # 0.2 token wait, then the remainder of the window that opened during it
        # (2.0 declared at t=0, 0.2 already elapsed), then a fresh token
        # interval from the emptied bucket.
        assert clock.sleeps == pytest.approx([0.2, 1.8, 0.2])
        assert clock.now == pytest.approx(2.2)


@pytest.fixture
def clean_registry() -> Iterator[None]:
    """Isolate the process-wide limiter cache from other tests."""
    rate_limiting._LIMITERS.clear()
    yield
    rate_limiting._LIMITERS.clear()


@pytest.mark.usefixtures("clean_registry")
class TestLimiterRegistry:
    """One shared limiter per configured service; None (cached) otherwise."""

    def test_returns_same_instance_for_a_service(self):
        first = get_connector_rate_limiter("spotify")
        second = get_connector_rate_limiter("spotify")

        assert first is not None
        assert first is second
        assert first.rate_per_second == settings.api.spotify.rate_limit

    def test_distinct_services_get_distinct_limiters(self):
        assert get_connector_rate_limiter("spotify") is not get_connector_rate_limiter(
            "lastfm"
        )

    def test_service_without_a_configured_rate_limit_is_unpaced(self):
        assert settings.api.musicbrainz.rate_limit is None
        assert get_connector_rate_limiter("musicbrainz") is None
        assert rate_limiting._LIMITERS["musicbrainz"] is None

    def test_unknown_service_is_unpaced(self):
        assert get_connector_rate_limiter("not_a_connector") is None

    def test_non_connector_settings_attribute_is_unpaced(self):
        # settings.api also carries scalars like spotify_market.
        assert get_connector_rate_limiter("spotify_market") is None
