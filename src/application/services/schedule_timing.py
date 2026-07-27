"""DST-correct next-run computation for daily/weekly/interval schedules.

Pure function with an injected ``now`` (no wall-clock reads) so tests are
deterministic without freezegun. The wall-clock cadences (``hour:minute`` daily,
or on a ``day_of_week`` weekly) are evaluated against a timezone-aware base so DST
is handled correctly — "6:30am Sunday" stays 6:30am local across spring-forward /
fall-back, and an awkward time like a 2:30am daily sync on a spring-forward day
resolves sanely.

``croniter`` is used purely as the internal next-occurrence engine — the user
never sees a cron expression; we build ``"{minute} {hour} * * {dow}"`` from the
entity's simple fields. Computed strictly forward from ``now`` (never iterating
from a stale stored value) and truncated to the whole minute, so the
repository's optimistic ``next_run_at`` equality claim holds.

The interval cadence takes a separate branch rather than a cron expression,
because an arbitrary "every N minutes" has no cron form (cron can express every
15 minutes, not every 37). It needs no timezone reasoning either: an elapsed
duration is unaffected by DST, which is precisely why a self-tuning poller uses
it rather than a wall-clock time it would have to keep rewriting.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from croniter import croniter

from src.domain.entities.schedule import Schedule


def compute_next_run(schedule: Schedule, *, now: datetime) -> datetime:
    """Next UTC fire time strictly after ``now`` (whole-minute precision).

    Used both to seed ``next_run_at`` on create/enable and to advance it after a
    fire — "advancing" is just recomputing forward from ``now``, which inherently
    skips all missed fires (the catchup *decision* lives in the scheduler service).
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    if schedule.interval_minutes is not None:
        # Truncated to the whole minute like the cron path, so the repository's
        # optimistic next_run_at equality claim holds for interval schedules too.
        nxt = now.astimezone(UTC) + timedelta(minutes=schedule.interval_minutes)
        return nxt.replace(second=0, microsecond=0)

    # day_of_week None ⇒ daily ("* * *"); set ⇒ weekly on that cron weekday.
    dow = "*" if schedule.day_of_week is None else str(schedule.day_of_week)
    cron = f"{schedule.minute} {schedule.hour} * * {dow}"

    base = now.astimezone(ZoneInfo(schedule.timezone))
    nxt = croniter(cron, base).get_next(datetime)
    return nxt.astimezone(UTC).replace(second=0, microsecond=0)
