"""Unit tests for daily/weekly next-run computation.

Time-injected (no freezegun). Covers daily roll-over, weekly weekday selection,
DST-correctness across a fall-back boundary, whole-minute truncation, and the
tz-aware guard.
"""

from datetime import UTC, datetime
from uuid import uuid7

import pytest

from src.application.services.schedule_timing import compute_next_run
from src.domain.entities.schedule import Schedule


def _schedule(
    *, hour: int, minute: int, tz: str, day_of_week: int | None = None
) -> Schedule:
    return Schedule(
        user_id="u",
        workflow_id=uuid7(),
        hour=hour,
        minute=minute,
        day_of_week=day_of_week,
        timezone=tz,
    )


class TestDaily:
    def test_later_today(self) -> None:
        s = _schedule(hour=2, minute=0, tz="UTC")
        nxt = compute_next_run(s, now=datetime(2026, 6, 1, 1, 0, tzinfo=UTC))
        assert nxt == datetime(2026, 6, 1, 2, 0, tzinfo=UTC)

    def test_rolls_to_tomorrow_when_past(self) -> None:
        s = _schedule(hour=2, minute=0, tz="UTC")
        nxt = compute_next_run(s, now=datetime(2026, 6, 1, 3, 0, tzinfo=UTC))
        assert nxt == datetime(2026, 6, 2, 2, 0, tzinfo=UTC)


class TestWeeklyDST:
    def test_sunday_630_local_before_fallback_is_utc_1330(self) -> None:
        # Oct 25 2026 is PDT (UTC-7): 6:30am local → 13:30 UTC.
        s = _schedule(hour=6, minute=30, day_of_week=0, tz="America/Los_Angeles")
        nxt = compute_next_run(s, now=datetime(2026, 10, 20, 12, 0, tzinfo=UTC))
        assert nxt == datetime(2026, 10, 25, 13, 30, tzinfo=UTC)

    def test_sunday_630_local_after_fallback_is_utc_1430(self) -> None:
        # Nov 1 2026 fall-back → PST (UTC-8): the SAME 6:30am local → 14:30 UTC.
        # Proves DST is honored: local time stable, the UTC instant shifts.
        s = _schedule(hour=6, minute=30, day_of_week=0, tz="America/Los_Angeles")
        nxt = compute_next_run(s, now=datetime(2026, 10, 27, 12, 0, tzinfo=UTC))
        assert nxt == datetime(2026, 11, 1, 14, 30, tzinfo=UTC)

    def test_picks_the_named_weekday(self) -> None:
        # day_of_week=3 is Wednesday (cron 0=Sun). From a Monday, next is Wed.
        s = _schedule(hour=9, minute=0, day_of_week=3, tz="UTC")
        nxt = compute_next_run(s, now=datetime(2026, 6, 1, 0, 0, tzinfo=UTC))  # Mon
        assert nxt == datetime(2026, 6, 3, 9, 0, tzinfo=UTC)  # Wed


class TestPrecisionAndGuards:
    def test_result_is_utc_whole_minute(self) -> None:
        s = _schedule(hour=6, minute=30, tz="America/Los_Angeles")
        nxt = compute_next_run(s, now=datetime(2026, 6, 1, 0, 0, 17, 500, tzinfo=UTC))
        assert nxt.tzinfo is UTC
        assert nxt.second == 0
        assert nxt.microsecond == 0

    def test_naive_now_rejected(self) -> None:
        naive = datetime(2026, 6, 1, 0, 0)  # noqa: DTZ001  # the input under test
        with pytest.raises(ValueError, match="now must be timezone-aware"):
            compute_next_run(_schedule(hour=6, minute=0, tz="UTC"), now=naive)
