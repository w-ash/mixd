"""Unit tests for the shared exponential-backoff policy.

Covers growth, the cap, jitter determinism, and the guards that stop a
misconfigured policy from silently producing a nonsense cadence.
"""

import pytest

from src.domain.services.backoff import BackoffPolicy

# No jitter: growth and cap assertions should be exact, not approximate.
_EXACT = BackoffPolicy(base_seconds=60, cap_seconds=3600, jitter_ratio=0.0)


class TestGrowth:
    def test_zero_consecutive_returns_base(self) -> None:
        assert _EXACT.next_interval(0) == 60

    def test_doubles_per_unproductive_attempt(self) -> None:
        assert [_EXACT.next_interval(n) for n in (1, 2, 3, 4)] == [120, 240, 480, 960]

    def test_clamps_at_cap(self) -> None:
        assert _EXACT.next_interval(20) == 3600

    def test_enormous_counter_does_not_overflow(self) -> None:
        # A counter that ran away (or a corrupted stored value) must still yield
        # the cap rather than inf — the exponent is clamped before the power.
        assert _EXACT.next_interval(100_000) == 3600

    def test_negative_consecutive_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            _EXACT.next_interval(-1)


class TestJitter:
    def test_is_deterministic_for_a_key(self) -> None:
        policy = BackoffPolicy(base_seconds=600, cap_seconds=6000)
        assert policy.next_interval(3, key="user-a") == policy.next_interval(
            3, key="user-a"
        )

    def test_differs_between_keys(self) -> None:
        # The whole point: two users converging on the same cap must not fire at
        # the same instant. (Chosen keys, since a hash could coincide.)
        policy = BackoffPolicy(base_seconds=600, cap_seconds=6000)
        spread = {policy.next_interval(5, key=f"user-{i}") for i in range(20)}
        assert len(spread) > 1

    def test_stays_within_the_configured_ratio(self) -> None:
        policy = BackoffPolicy(base_seconds=1000, cap_seconds=1000, jitter_ratio=0.1)
        for i in range(50):
            assert 900 <= policy.next_interval(0, key=f"user-{i}") <= 1100

    def test_empty_key_disables_jitter(self) -> None:
        policy = BackoffPolicy(base_seconds=600, cap_seconds=6000, jitter_ratio=0.5)
        assert policy.next_interval(0) == 600

    def test_never_returns_below_one_second(self) -> None:
        policy = BackoffPolicy(base_seconds=1, cap_seconds=1, jitter_ratio=0.9)
        assert all(policy.next_interval(0, key=f"u{i}") >= 1 for i in range(50))


class TestConfigGuards:
    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"base_seconds": 0, "cap_seconds": 10}, "base_seconds"),
            ({"base_seconds": 100, "cap_seconds": 10}, "cap_seconds"),
            ({"base_seconds": 10, "cap_seconds": 100, "factor": 0.5}, "factor"),
            ({"base_seconds": 10, "cap_seconds": 100, "jitter_ratio": 1.0}, "jitter"),
        ],
    )
    def test_invalid_config_rejected(
        self, kwargs: dict[str, float], match: str
    ) -> None:
        with pytest.raises(ValueError, match=match):
            BackoffPolicy(**kwargs)  # pyright: ignore[reportArgumentType]
