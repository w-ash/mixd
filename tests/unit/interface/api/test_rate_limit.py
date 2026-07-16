"""Unit tests for the in-memory sliding-window chat rate limiter.

Covers the sliding-window cap and the opportunistic stale-key sweep that keeps
``_hits`` from growing without bound as new per-user keys accumulate (R8).
"""

import pytest

from src.domain.exceptions import RateLimitExceededError
from src.interface.api.rate_limit import InMemoryRateLimiter


class TestSlidingWindow:
    def test_allows_up_to_max_then_raises(self) -> None:
        limiter = InMemoryRateLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            limiter.check("u1")
        with pytest.raises(RateLimitExceededError):
            limiter.check("u1")

    def test_keys_are_independent(self) -> None:
        limiter = InMemoryRateLimiter(max_requests=1, window_seconds=60)
        limiter.check("u1")
        limiter.check("u2")  # separate bucket — must not raise

    def test_window_slides_as_time_advances(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = {"now": 1000.0}
        monkeypatch.setattr(
            "src.interface.api.rate_limit.time.monotonic", lambda: clock["now"]
        )
        limiter = InMemoryRateLimiter(max_requests=1, window_seconds=10)
        limiter.check("u1")
        clock["now"] = 1011.0  # past the window
        limiter.check("u1")  # old hit expired — must not raise


class TestStaleKeySweep:
    def test_idle_keys_are_reclaimed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = {"now": 1000.0}
        monkeypatch.setattr(
            "src.interface.api.rate_limit.time.monotonic", lambda: clock["now"]
        )
        limiter = InMemoryRateLimiter(max_requests=5, window_seconds=60)
        # A burst of one-shot users, each seen once and never again.
        for i in range(50):
            limiter.check(f"user-{i}")
        assert len(limiter._hits) == 50

        # Advance past window + sweep interval, then touch one key to trigger it.
        clock["now"] = 1000.0 + 60 + limiter._SWEEP_INTERVAL_SECONDS + 1
        limiter.check("live")

        # All the idle one-shot keys are gone; only the live one remains.
        assert set(limiter._hits) == {"live"}

    def test_sweep_keeps_keys_seen_within_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = {"now": 1000.0}
        monkeypatch.setattr(
            "src.interface.api.rate_limit.time.monotonic", lambda: clock["now"]
        )
        limiter = InMemoryRateLimiter(max_requests=5, window_seconds=60)
        limiter.check("recent")
        # Jump far enough to run the sweep, but keep "recent" fresh first.
        clock["now"] = 1000.0 + limiter._SWEEP_INTERVAL_SECONDS + 1
        limiter.check("recent")  # refresh within window, then sweep fires
        assert "recent" in limiter._hits
