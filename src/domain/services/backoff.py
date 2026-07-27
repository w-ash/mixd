"""Shared exponential-backoff policy for persisted, cross-restart schedules.

A counter plus a time in, a next-due time out. Deliberately *not* ``tenacity``,
which this project already depends on: that library models an in-process retry
loop, deriving its wait from ``RetryCallState.attempt_number`` — state that dies
with the process. Here the counter lives in a database column and outlives every
restart, so the two solve different problems that happen to share the doubling
formula. (Domain purity forbids the import besides: this layer takes stdlib and
domain only.)

Consumers so far: the play poller's stretch-on-empty (v0.10.1). v0.10.2's
negative-cache retry and identity-death debounce are the next two, with their own
constants — which is the point of putting the arithmetic in one place rather than
growing three subtly divergent implementations across adjacent milestones.

**Jitter is deterministic, not random.** Callers pass a stable key (a user id);
the same key always yields the same offset. Randomness would make the function
untestable and non-reproducible, while an *un*-jittered interval is worse than
either: every consumer converging on the same cap fires at the same instant, and
on a scale-to-zero database a synchronised wake is a synchronised bill.
"""

from hashlib import blake2b
from typing import Final

from attrs import define

# Ratio of the interval that jitter may shift it by, in either direction. Small
# enough not to disturb the cadence, wide enough to decorrelate a fleet.
DEFAULT_JITTER_RATIO: Final = 0.1


def _jitter_fraction(key: str) -> float:
    """Stable value in [-1.0, 1.0) derived from ``key``.

    A hash rather than ``hash()``: the built-in is salted per process, so the
    same user would land in a different slot after every restart — precisely the
    decorrelation this is meant to make permanent.
    """
    if not key:
        return 0.0
    digest = blake2b(key.encode("utf-8"), digest_size=4).digest()
    # 2**32, not 2**32-1: dividing by the max value makes the range closed at
    # both ends, so an all-ones digest yields exactly +1.0 and next_interval
    # could exceed the cap-plus-jitter bound its docstring promises.
    unit = int.from_bytes(digest, "big") / 0x100000000  # [0.0, 1.0)
    return unit * 2.0 - 1.0


@define(frozen=True, slots=True)
class BackoffPolicy:
    """Doubling interval from ``base_seconds`` up to ``cap_seconds``.

    ``consecutive`` counts *unproductive* attempts: 0 means "the last attempt
    found something", so the interval returns to base. Growth is
    ``base * factor ** consecutive``, clamped at the cap, then jittered.
    """

    base_seconds: int
    cap_seconds: int
    factor: float = 2.0
    jitter_ratio: float = DEFAULT_JITTER_RATIO

    def __attrs_post_init__(self) -> None:
        if self.base_seconds <= 0:
            raise ValueError("base_seconds must be positive")
        if self.cap_seconds < self.base_seconds:
            raise ValueError("cap_seconds must be >= base_seconds")
        if self.factor < 1.0:
            raise ValueError("factor must be >= 1.0 (a shrinking backoff is a bug)")
        if not 0.0 <= self.jitter_ratio < 1.0:
            raise ValueError("jitter_ratio must be in [0.0, 1.0)")

    def next_interval(self, consecutive: int, *, key: str = "") -> int:
        """Seconds to wait after ``consecutive`` unproductive attempts.

        Never returns less than one second, and never exceeds the cap plus its
        jitter — a caller can size a claim TTL against the cap and trust it.
        """
        if consecutive < 0:
            raise ValueError("consecutive must be non-negative")
        # Clamp the exponent before computing the power: a large counter would
        # otherwise overflow to inf on its way to being capped anyway.
        max_doublings = self._doublings_to_cap()
        grown = self.base_seconds * self.factor ** min(consecutive, max_doublings)
        capped = min(grown, float(self.cap_seconds))
        jittered = capped * (1.0 + self.jitter_ratio * _jitter_fraction(key))
        return max(1, round(jittered))

    def _doublings_to_cap(self) -> int:
        """Smallest exponent whose growth already reaches the cap."""
        if self.factor <= 1.0:
            return 0
        doublings = 0
        value = float(self.base_seconds)
        while value < self.cap_seconds:
            value *= self.factor
            doublings += 1
        return doublings
