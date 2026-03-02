"""Lightweight execution timer for use case metrics."""

from datetime import UTC, datetime
from typing import final


@final
class ExecutionTimer:
    """Lightweight timer for use case execution metrics.

    Replaces the repeated pattern of datetime.now(UTC) subtraction or time.time()
    arithmetic scattered across use cases. Provides consistent millisecond precision.

    Usage:
        timer = ExecutionTimer()
        # ... work ...
        result = SomeResult(execution_time_ms=timer.stop())
    """

    __slots__ = ("_start", "elapsed_ms")

    def __init__(self) -> None:
        self._start = datetime.now(UTC)
        self.elapsed_ms = 0

    def stop(self) -> int:
        """Stop timer and return elapsed milliseconds."""
        self.elapsed_ms = int((datetime.now(UTC) - self._start).total_seconds() * 1000)
        return self.elapsed_ms
