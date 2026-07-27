"""Schedule domain entity.

A schedule fires a workflow run or a background sync on a simple calendar
cadence — **daily** at a chosen time, or **weekly** on a chosen day at a chosen
time — in the user's IANA timezone, at minute granularity. Exactly one target is
set: ``workflow_id`` XOR ``sync_target``.

The entity is pure: it stores ``timezone`` as a plain string and ``next_run_at``
as a pre-computed field. The application layer owns ``zoneinfo``/``croniter``
(forbidden in the domain by ``domain-purity.md``) and computes ``next_run_at``
from ``(hour, minute, day_of_week)`` — see
``application/services/schedule_timing.py``.

Cadence is intentionally minimal (no freeform cron, no sub-daily intervals):
``schedule_type`` is *derived* from whether ``day_of_week`` is set, so "daily"
vs "weekly" is never a separate field that could drift out of sync.
"""

from datetime import datetime
from typing import Final, Literal
from uuid import UUID, uuid7

from attrs import define, field

from .shared import validate_timezone_aware

type ScheduleType = Literal["daily", "weekly", "interval"]
type ScheduleStatus = Literal["enabled", "disabled"]
type ScheduleTargetType = Literal["workflow", "sync"]

# day_of_week uses the cron convention: 0 = Sunday … 6 = Saturday. Interface
# layers map a weekday name to this int (and back) for display.
_MAX_HOUR: Final = 23
_MAX_MINUTE: Final = 59
_MIN_DAY_OF_WEEK: Final = 0
_MAX_DAY_OF_WEEK: Final = 6
# Interval bounds mirror the CHECK in migration 043. The floor is what the
# constraint permits, not what any caller should choose: a 5-minute cadence
# holds a scale-to-zero compute awake continuously.
MIN_INTERVAL_MINUTES: Final = 5
MAX_INTERVAL_MINUTES: Final = 1440


def validate_time_of_day(hour: int, minute: int) -> tuple[int, int]:
    """Return ``(hour, minute)`` if a valid wall-clock time, else raise.

    Pure domain rule reused by the entity (backstop) and the CLI (fast-fail) —
    the ``normalize_tag`` pattern. The API expresses the same bounds as Pydantic
    ``Field`` constraints.
    """
    if not 0 <= hour <= _MAX_HOUR:
        raise ValueError(f"hour must be 0-23, got {hour}")
    if not 0 <= minute <= _MAX_MINUTE:
        raise ValueError(f"minute must be 0-59, got {minute}")
    return hour, minute


def validate_day_of_week(day_of_week: int) -> int:
    """Return ``day_of_week`` if 0 (Sunday)…6 (Saturday), else raise."""
    if not _MIN_DAY_OF_WEEK <= day_of_week <= _MAX_DAY_OF_WEEK:
        raise ValueError(
            f"day_of_week must be 0 (Sunday) to 6 (Saturday), got {day_of_week}"
        )
    return day_of_week


def validate_interval_minutes(interval_minutes: int) -> int:
    """Return ``interval_minutes`` if within the supported range, else raise."""
    if not MIN_INTERVAL_MINUTES <= interval_minutes <= MAX_INTERVAL_MINUTES:
        raise ValueError(
            f"interval_minutes must be {MIN_INTERVAL_MINUTES}-{MAX_INTERVAL_MINUTES}, "
            f"got {interval_minutes}"
        )
    return interval_minutes


@define(frozen=True, slots=True)
class Schedule:
    """An automated trigger for a workflow run or a background sync.

    Cadence:
    - ``day_of_week is None`` → **daily** at ``hour:minute`` (local).
    - ``day_of_week`` set (0=Sun…6=Sat) → **weekly** on that day at ``hour:minute``.
    - ``interval_minutes`` set → **interval**: every N minutes, ignoring
      ``hour``/``minute``. Used by the adaptive play poller, which rewrites its
      own interval after each poll so the scheduler sleeps for the backed-off
      duration instead of waking on a fixed tick to find nothing to do.

    Invariants (enforced in ``__attrs_post_init__``):
    - Exactly one of ``workflow_id`` / ``sync_target`` is set (exclusive arc).
    - ``hour`` in 0-23, ``minute`` in 0-59, ``day_of_week`` is None or 0-6.
    - ``interval_minutes`` is None or 5-1440, and never set alongside
      ``day_of_week`` (one cadence at a time).
    """

    user_id: str
    # Exclusive arc — exactly one target is set.
    workflow_id: UUID | None = None
    sync_target: str | None = None
    # Cadence — minute granularity, in `timezone`. day_of_week None ⇒ daily.
    hour: int = 0
    minute: int = 0
    day_of_week: int | None = None
    # Third cadence arm — exclusive with day_of_week.
    interval_minutes: int | None = None
    # IANA zone name (e.g. "America/Los_Angeles"); validated in the app layer.
    timezone: str = "UTC"
    status: ScheduleStatus = "enabled"
    # Pre-computed UTC fire time (set by the application's compute_next_run).
    next_run_at: datetime | None = field(
        default=None, validator=validate_timezone_aware
    )
    # Reaper claim marker: non-null while a dispatch is in flight.
    started_at: datetime | None = field(default=None, validator=validate_timezone_aware)
    # Last-run observability.
    last_run_at: datetime | None = field(
        default=None, validator=validate_timezone_aware
    )
    last_run_status: str | None = None
    last_error: str | None = None
    last_run_id: UUID | None = None
    run_count: int = 0
    consecutive_failures: int = 0
    created_at: datetime | None = field(default=None, validator=validate_timezone_aware)
    updated_at: datetime | None = field(default=None, validator=validate_timezone_aware)
    id: UUID = field(factory=uuid7)

    def __attrs_post_init__(self) -> None:
        # Exclusive arc: exactly one target. Two distinct messages so the
        # caller knows which way the invariant failed.
        if self.workflow_id is not None and self.sync_target is not None:
            raise ValueError(
                "schedule must target exactly one of workflow_id or sync_target, "
                "both were provided"
            )
        if self.workflow_id is None and self.sync_target is None:
            raise ValueError(
                "schedule must target exactly one of workflow_id or sync_target, "
                "neither was provided"
            )
        # Wall-clock time + optional weekday (the pure domain validators).
        validate_time_of_day(self.hour, self.minute)
        if self.day_of_week is not None:
            validate_day_of_week(self.day_of_week)
        # Exclusive cadence arc, mirroring ck_schedules_cadence_exclusive.
        if self.interval_minutes is not None:
            if self.day_of_week is not None:
                raise ValueError(
                    "schedule cadence must be one of daily, weekly, or interval; "
                    "day_of_week and interval_minutes were both provided"
                )
            validate_interval_minutes(self.interval_minutes)
        # Observability counters can never be negative.
        if self.run_count < 0:
            raise ValueError("run_count must be non-negative")
        if self.consecutive_failures < 0:
            raise ValueError("consecutive_failures must be non-negative")

    @property
    def schedule_type(self) -> ScheduleType:
        """Derived cadence kind — never stored."""
        if self.interval_minutes is not None:
            return "interval"
        return "weekly" if self.day_of_week is not None else "daily"

    @property
    def target_type(self) -> ScheduleTargetType:
        """Derived target discriminator — never stored in the domain."""
        return "workflow" if self.workflow_id is not None else "sync"
