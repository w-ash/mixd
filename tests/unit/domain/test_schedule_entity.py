"""Unit tests for the Schedule domain entity.

Pure constructor-invariant tests: the exclusive arc (workflow_id XOR
sync_target), derived daily/weekly cadence, wall-clock + weekday ranges,
timezone-aware datetimes, and non-negative counters. No I/O, no mocks.
"""

from datetime import datetime
from uuid import uuid7

import pytest

from src.domain.entities.schedule import (
    Schedule,
    validate_day_of_week,
    validate_time_of_day,
)


def _daily_workflow(**overrides: object) -> Schedule:
    """A minimal valid daily workflow schedule; override any field."""
    kwargs: dict[str, object] = {
        "user_id": "u",
        "workflow_id": uuid7(),
        "hour": 6,
        "minute": 30,
        "timezone": "America/Los_Angeles",
    }
    kwargs.update(overrides)
    return Schedule(**kwargs)  # type: ignore[arg-type]


class TestExclusiveArc:
    def test_both_targets_set_raises(self) -> None:
        with pytest.raises(ValueError, match="both were provided"):
            Schedule(user_id="u", workflow_id=uuid7(), sync_target="lastfm:plays")

    def test_neither_target_set_raises(self) -> None:
        with pytest.raises(ValueError, match="neither was provided"):
            Schedule(user_id="u", hour=6)

    def test_workflow_target_derives_target_type(self) -> None:
        assert _daily_workflow().target_type == "workflow"

    def test_sync_target_derives_target_type(self) -> None:
        schedule = Schedule(user_id="u", sync_target="lastfm:plays", hour=2)
        assert schedule.target_type == "sync"


class TestCadence:
    def test_no_day_of_week_is_daily(self) -> None:
        assert _daily_workflow().schedule_type == "daily"

    def test_day_of_week_set_is_weekly(self) -> None:
        assert _daily_workflow(day_of_week=0).schedule_type == "weekly"


class TestTimeAndDayRanges:
    def test_hour_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="hour must be 0"):
            _daily_workflow(hour=24)

    def test_minute_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="minute must be 0"):
            _daily_workflow(minute=60)

    def test_day_of_week_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="day_of_week must be"):
            _daily_workflow(day_of_week=7)

    def test_valid_weekday_accepted(self) -> None:
        assert _daily_workflow(day_of_week=6).day_of_week == 6


class TestFieldInvariants:
    def test_naive_next_run_at_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be timezone-aware"):
            _daily_workflow(next_run_at=datetime(2026, 6, 1, 6, 0))  # noqa: DTZ001

    def test_negative_consecutive_failures_raises(self) -> None:
        with pytest.raises(ValueError, match="consecutive_failures must be"):
            _daily_workflow(consecutive_failures=-1)


class TestPureValidators:
    def test_validate_time_of_day_returns_pair(self) -> None:
        assert validate_time_of_day(6, 30) == (6, 30)

    def test_validate_time_of_day_rejects_bad_minute(self) -> None:
        with pytest.raises(ValueError, match="minute must be 0"):
            validate_time_of_day(6, 99)

    def test_validate_day_of_week_bounds(self) -> None:
        assert validate_day_of_week(0) == 0
        assert validate_day_of_week(6) == 6
        with pytest.raises(ValueError, match="day_of_week must be"):
            validate_day_of_week(-1)
