"""Schedule repository — workflow/sync calendar triggers (v0.8.2).

Two distinct access patterns share one table:

- **CRUD** (``create`` / ``update`` / ``delete`` / ``get_by_id`` /
  ``get_for_target`` / ``list_for_user``) is per-user: every method filters by
  ``user_id`` because the ``schedules`` table has NO row-level security (it
  mirrors ``workflow_runs``). Forgetting the filter would leak cross-tenant rows.
- **System hot-path** (``find_due_schedules`` / ``mark_schedule_*`` /
  ``list_stuck_started``) is cross-tenant: the scheduler polls every user's due
  rows in one query, so these methods deliberately take no ``user_id``.

The load-bearing piece is ``mark_schedule_started``'s optimistic claim
(``WHERE … started_at IS NULL`` → ``rowcount > 0``): it lets exactly one poller
across a multi-machine deploy win a due schedule without holding a row lock,
reusing the v0.8.0 guarded-UPDATE idiom from ``workflow/runs.py``.
"""

# pyright: reportAttributeAccessIssue=false, reportUnknownMemberType=false
# Legitimate: CursorResult.rowcount is valid but invisible to pyright through
# the generic Result[Any] returned by session.execute().

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.config.constants import (
    SchedulerConstants,
    WorkflowConstants,
    truncate_error_message,
)
from src.domain.entities.schedule import Schedule
from src.domain.exceptions import (
    NotFoundError,
    ScheduleAlreadyExistsError,
    ScheduleInvariantError,
)
from src.infrastructure.persistence.database.db_models import DBSchedule
from src.infrastructure.persistence.repositories.base_repo import (
    BaseRepository,
    SimpleMapperFactory,
)
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)

ScheduleMapper = SimpleMapperFactory.create(DBSchedule, Schedule)

# The two partial-unique indexes that enforce one-schedule-per-target. A
# collision on either means the user already has a schedule for this target.
_TARGET_UNIQUE_CONSTRAINTS = frozenset({
    "uq_schedules_workflow_target",
    "uq_schedules_sync_target",
})

# The CHECK constraints (migration 025 only — never in __table_args__): the
# exclusive target arc and the cadence-range bounds. A violation is a malformed
# schedule (→ 422), not a server fault (→ 500).
_CHECK_CONSTRAINTS = frozenset({
    "ck_schedules_target_xor",
    "ck_schedules_time_of_day",
    "ck_schedules_day_of_week",
    "ck_schedules_valid_status",
    "ck_schedules_counts_nonneg",
})


def _violated_constraint(exc: IntegrityError) -> str | None:
    """The DB constraint name an ``IntegrityError`` violated, if psycopg reports it.

    psycopg populates ``orig.diag.constraint_name`` for constraint violations, so
    matching by name (not a brittle message substring) keeps the classification
    narrow — an unrelated integrity error returns ``None`` and surfaces as a real
    failure rather than a mis-mapped 4xx.
    """
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    constraint = getattr(diag, "constraint_name", None)
    return constraint if isinstance(constraint, str) else None


def _target_label(schedule: Schedule) -> str:
    """Human-readable target id for error bodies (workflow id or sync target)."""
    if schedule.workflow_id is not None:
        return str(schedule.workflow_id)
    return str(schedule.sync_target)


def _raise_for_constraint(exc: IntegrityError, *, target: str) -> None:
    """Map a ``schedules`` IntegrityError to a domain exception, or return.

    A one-per-target unique violation → ``ScheduleAlreadyExistsError`` (409); a
    CHECK violation → ``ScheduleInvariantError`` (422). Returns for anything else
    so the caller re-raises the original (a genuine 500).
    """
    constraint = _violated_constraint(exc)
    if constraint in _TARGET_UNIQUE_CONSTRAINTS:
        raise ScheduleAlreadyExistsError(target) from exc
    if constraint in _CHECK_CONSTRAINTS:
        raise ScheduleInvariantError(constraint) from exc


class ScheduleRepository(BaseRepository[DBSchedule, Schedule]):
    """Persistence for ``schedules`` rows — CRUD plus the scheduler hot-path."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(
            session=session,
            model_class=DBSchedule,
            mapper=ScheduleMapper(),
        )

    # ------------------------------------------------------------------
    # CRUD — every method is user-scoped (no RLS on this table).
    # ------------------------------------------------------------------

    @db_operation("create_schedule")
    async def create(self, schedule: Schedule) -> Schedule:
        """Insert a new schedule row.

        ``target_type`` is NOT stored — it is derived on the entity from
        ``workflow_id``, so only the columns below are written. A partial-unique
        collision means the user already has a schedule for this target →
        ``ScheduleAlreadyExistsError``.
        """
        db_row = DBSchedule(
            id=schedule.id,
            user_id=schedule.user_id,
            workflow_id=schedule.workflow_id,
            sync_target=schedule.sync_target,
            hour=schedule.hour,
            minute=schedule.minute,
            day_of_week=schedule.day_of_week,
            timezone=schedule.timezone,
            status=schedule.status,
            next_run_at=schedule.next_run_at,
            started_at=schedule.started_at,
            last_run_at=schedule.last_run_at,
            last_run_status=schedule.last_run_status,
            last_error=schedule.last_error,
            last_run_id=schedule.last_run_id,
            run_count=schedule.run_count,
            consecutive_failures=schedule.consecutive_failures,
        )
        self.session.add(db_row)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            _raise_for_constraint(exc, target=_target_label(schedule))
            raise
        return await ScheduleMapper.to_domain(db_row)

    @db_operation("update_schedule")
    async def update_schedule(self, schedule: Schedule, *, user_id: str) -> Schedule:
        """Overwrite the *user-owned* fields of an existing schedule (user-scoped).

        Column ownership is split so the lock-free claim can't be corrupted:

        - **User-owned** (written here): cadence (``hour``/``minute``/
          ``day_of_week``/``timezone``), ``status``, and the cadence-derived
          ``next_run_at`` the use case recomputes on a cadence change.
        - **Scheduler-owned** (NEVER written here): the claim/run-bookkeeping
          columns (``started_at``, ``last_run_*``, ``last_error``, ``run_count``,
          ``consecutive_failures``) belong solely to the claim-guarded
          ``mark_schedule_*`` path. A CRUD edit landing mid-dispatch therefore
          can't clear ``started_at`` and resurrect a claimed schedule, and run
          history is preserved structurally (not by copying old values forward).

        The target arc (``workflow_id`` / ``sync_target``) is fixed at create and
        never written. Guarded by ``id AND user_id`` so a cross-tenant id can't
        update another user's row.
        """
        values: dict[str, object] = {
            "hour": schedule.hour,
            "minute": schedule.minute,
            "day_of_week": schedule.day_of_week,
            "timezone": schedule.timezone,
            "status": schedule.status,
            "next_run_at": schedule.next_run_at,
            "updated_at": datetime.now(UTC),
        }
        stmt = (
            update(DBSchedule)
            .where(DBSchedule.id == schedule.id, DBSchedule.user_id == user_id)
            .values(**values)
            .returning(DBSchedule)
        )
        try:
            result = await self.session.execute(stmt)
        except IntegrityError as exc:
            # A cadence edit can violate a range CHECK (hour/minute/day_of_week) →
            # 422, not 500. The target arc is fixed at create, so no unique clash.
            _raise_for_constraint(exc, target=_target_label(schedule))
            raise
        db_row = result.scalar_one_or_none()
        if db_row is None:
            raise NotFoundError(f"Schedule {schedule.id} not found")
        return await ScheduleMapper.to_domain(db_row)

    @db_operation("delete_schedule")
    async def delete_for_user(self, schedule_id: UUID, *, user_id: str) -> bool:
        """Delete one schedule. Returns ``True`` if a row was removed.

        Returning a bool (rather than raising) lets the route answer 404 for
        both not-found and not-owner without leaking row existence.
        """
        stmt = (
            delete(DBSchedule)
            .where(DBSchedule.id == schedule_id, DBSchedule.user_id == user_id)
            .returning(DBSchedule.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    @db_operation("get_schedule_by_id")
    async def get_by_id_for_user(
        self, schedule_id: UUID, *, user_id: str
    ) -> Schedule | None:
        """Return the schedule if it exists AND is owned by ``user_id``, else None."""
        stmt = select(DBSchedule).where(
            DBSchedule.id == schedule_id, DBSchedule.user_id == user_id
        )
        result = await self.session.execute(stmt)
        db_row = result.scalar_one_or_none()
        if db_row is None:
            return None
        return await ScheduleMapper.to_domain(db_row)

    @db_operation("get_schedule_for_target")
    async def get_for_target(
        self,
        *,
        user_id: str,
        workflow_id: UUID | None = None,
        sync_target: str | None = None,
    ) -> Schedule | None:
        """Return this user's schedule for a single target, or None.

        Exactly one of ``workflow_id`` / ``sync_target`` must be given — the
        upsert use case calls this to decide create vs. replace. The DB's
        partial-unique indexes guarantee at most one match.
        """
        if (workflow_id is None) == (sync_target is None):
            raise ValueError(
                "get_for_target requires exactly one of workflow_id / sync_target"
            )
        stmt = select(DBSchedule).where(DBSchedule.user_id == user_id)
        if workflow_id is not None:
            stmt = stmt.where(DBSchedule.workflow_id == workflow_id)
        else:
            stmt = stmt.where(DBSchedule.sync_target == sync_target)
        result = await self.session.execute(stmt)
        db_row = result.scalar_one_or_none()
        if db_row is None:
            return None
        return await ScheduleMapper.to_domain(db_row)

    @db_operation("list_schedules_for_user")
    async def list_for_user(self, *, user_id: str) -> list[Schedule]:
        """List all of a user's schedules, newest-first."""
        stmt = (
            select(DBSchedule)
            .where(DBSchedule.user_id == user_id)
            .order_by(DBSchedule.created_at.desc(), DBSchedule.id.desc())
        )
        result = await self.session.execute(stmt)
        db_rows = list(result.scalars().all())
        return [await ScheduleMapper.to_domain(r) for r in db_rows]

    # ------------------------------------------------------------------
    # System hot-path — cross-tenant; the scheduler reads ALL users' rows.
    # ------------------------------------------------------------------

    @db_operation("try_acquire_poll_lock")
    async def try_acquire_poll_lock(
        self, key: int = SchedulerConstants.POLL_LOCK_KEY
    ) -> bool:
        """Try to win this tick's cross-instance poll lock. ``True`` iff acquired.

        A *transaction*-level advisory lock (``pg_try_advisory_xact_lock``) taken at
        the top of the poll transaction: only the winner scans/reaps this tick, so
        N replicas don't each run N redundant cross-tenant scans. It auto-releases
        when the poll transaction commits (and on rollback/disconnect — it cannot
        leak), so there is no long-lived connection or standby loop to manage.
        Correctness against double-dispatch is the atomic ``mark_schedule_started``
        claim, independent of this lock; losing the lock just skips a redundant scan.
        """
        result = await self.session.execute(select(func.pg_try_advisory_xact_lock(key)))
        return cast("bool", result.scalar_one())

    @db_operation("find_due_schedules")
    async def find_due_schedules(
        self, now: datetime, *, limit: int = SchedulerConstants.DUE_BATCH_MAX
    ) -> list[Schedule]:
        """Enabled, unclaimed schedules whose fire time has arrived (all users).

        ``started_at IS NULL`` skips rows a concurrent tick already claimed —
        an optimization on top of the claim guard, not a substitute for it.
        Capped at ``DUE_BATCH_MAX`` (oldest-first) so a large backlog can't make
        one tick unbounded. Uses ``ix_schedules_status_next_run_at``.
        """
        stmt = (
            select(DBSchedule)
            .where(
                DBSchedule.status == "enabled",
                DBSchedule.next_run_at <= now,
                DBSchedule.started_at.is_(None),
            )
            .order_by(DBSchedule.next_run_at.asc())
            .limit(min(limit, SchedulerConstants.DUE_BATCH_MAX))
        )
        result = await self.session.execute(stmt)
        db_rows = list(result.scalars().all())
        return [await ScheduleMapper.to_domain(r) for r in db_rows]

    @db_operation("mark_schedule_started")
    async def mark_schedule_started(
        self,
        schedule_id: UUID,
        *,
        expected_next_run_at: datetime,
        now: datetime,
    ) -> bool:
        """Optimistically claim a due schedule for dispatch.

        LOAD-BEARING. The guard ``status='enabled' AND next_run_at=:expected AND
        started_at IS NULL`` means only the first of N racing pollers (possibly
        across machines) flips ``started_at`` and gets ``rowcount > 0``; the rest
        no-op and skip the schedule. ``started_at IS NULL`` is NOT optional:
        without it, a dispatch outliving the poll interval (``next_run_at`` only
        advances on completion) would be re-claimed every tick.
        """
        stmt = (
            update(DBSchedule)
            .where(
                DBSchedule.id == schedule_id,
                DBSchedule.status == "enabled",
                DBSchedule.next_run_at == expected_next_run_at,
                DBSchedule.started_at.is_(None),
            )
            .values(started_at=now, updated_at=now)
        )
        result = await self.session.execute(stmt)
        return cast("int", result.rowcount) > 0

    async def _release_and_advance(
        self,
        schedule_id: UUID,
        *,
        next_run_at: datetime,
        last_run_at: datetime,
        last_run_status: str,
        **extra: object,
    ) -> bool:
        """Release the claim and advance one schedule — shared by the mark_* trio.

        Holds the LOAD-BEARING ``started_at IS NOT NULL`` claim guard in one
        place: a run the reaper already failed-and-released can't resurrect state
        by finalizing late (``rowcount == 0`` → ``False``). The shared block
        clears ``started_at`` and advances ``next_run_at`` / ``last_run_at`` /
        ``last_run_status``; ``extra`` carries each caller's delta (counters,
        ``last_error``, ``last_run_id``).
        """
        stmt = (
            update(DBSchedule)
            .where(DBSchedule.id == schedule_id, DBSchedule.started_at.isnot(None))
            .values(
                started_at=None,
                next_run_at=next_run_at,
                last_run_at=last_run_at,
                last_run_status=last_run_status,
                updated_at=last_run_at,
                **extra,
            )
        )
        result = await self.session.execute(stmt)
        return cast("int", result.rowcount) > 0

    @db_operation("mark_schedule_completed")
    async def mark_schedule_completed(
        self,
        schedule_id: UUID,
        *,
        next_run_at: datetime,
        last_run_at: datetime,
        last_run_status: str,
        last_run_id: UUID | None = None,
    ) -> bool:
        """Record a successful fire: reset failures, bump ``run_count``, record
        last-run provenance, then release the claim and advance."""
        return await self._release_and_advance(
            schedule_id,
            next_run_at=next_run_at,
            last_run_at=last_run_at,
            last_run_status=last_run_status,
            last_run_id=last_run_id,
            last_error=None,
            consecutive_failures=0,
            run_count=DBSchedule.run_count + 1,
        )

    @db_operation("mark_schedule_skipped")
    async def mark_schedule_skipped(
        self,
        schedule_id: UUID,
        *,
        next_run_at: datetime,
        last_run_at: datetime,
        last_run_status: str,
        reset_failures: bool = False,
    ) -> bool:
        """Advance WITHOUT counting a run (workflow already running, missed window
        under ``catchup=False``, shutdown drain, or a reaped claim). Leaves the
        counters / ``last_error`` untouched by default so a skip neither inflates
        success nor trips the failure banner.

        ``reset_failures=True`` additionally clears the failure streak: used for
        ``skipped_already_running``, where a manual run holding the slot is proof
        the workflow is healthy right now, so a lingering banner would be stale.
        """
        extra: dict[str, object] = {}
        if reset_failures:
            extra["consecutive_failures"] = 0
            extra["last_error"] = None
        return await self._release_and_advance(
            schedule_id,
            next_run_at=next_run_at,
            last_run_at=last_run_at,
            last_run_status=last_run_status,
            **extra,
        )

    @db_operation("mark_schedule_failed")
    async def mark_schedule_failed(
        self,
        schedule_id: UUID,
        *,
        next_run_at: datetime,
        last_run_at: datetime,
        last_error: str,
        last_run_status: str = "failed",
    ) -> bool:
        """Record a failed fire: increment ``consecutive_failures`` (powers the
        failure banner) and store a truncated ``last_error``, then advance."""
        return await self._release_and_advance(
            schedule_id,
            next_run_at=next_run_at,
            last_run_at=last_run_at,
            last_run_status=last_run_status,
            last_error=truncate_error_message(
                last_error, WorkflowConstants.ERROR_MESSAGE_MAX_LENGTH
            ),
            consecutive_failures=DBSchedule.consecutive_failures + 1,
        )

    @db_operation("mark_schedule_disabled")
    async def mark_schedule_disabled(
        self, schedule_id: UUID, *, last_error: str
    ) -> bool:
        """Disable a claimed schedule and release its claim (cross-tenant).

        For an orphaned target (a connector removed from ``SYNC_DISPATCH`` while a
        schedule for it remains): flipping ``status`` to ``disabled`` drops it from
        the due poll, turning a forever-failing schedule into a one-time, surfaced
        event. Holds the same ``started_at IS NOT NULL`` claim-release guard as
        ``_release_and_advance`` so a reaped row can't be resurrected.
        """
        now = datetime.now(UTC)
        stmt = (
            update(DBSchedule)
            .where(DBSchedule.id == schedule_id, DBSchedule.started_at.isnot(None))
            .values(
                status="disabled",
                started_at=None,
                last_error=truncate_error_message(
                    last_error, WorkflowConstants.ERROR_MESSAGE_MAX_LENGTH
                ),
                last_run_status="disabled",
                last_run_at=now,
                updated_at=now,
            )
        )
        result = await self.session.execute(stmt)
        return cast("int", result.rowcount) > 0

    # NB: the cross-tenant ``get_by_id(id_)`` the scheduler uses to re-read a
    # claimed row's current cadence is inherited from ``BaseRepository`` (it filters
    # by id only, no ``user_id``, and raises ``NotFoundError`` if absent). The
    # user-scoped CRUD read is ``get_by_id_for_user`` above.

    @db_operation("list_stuck_started")
    async def list_stuck_started(
        self,
        timeout_seconds: int,
        *,
        now: datetime,
        limit: int = SchedulerConstants.DUE_BATCH_MAX,
    ) -> list[Schedule]:
        """Schedules claimed longer ago than ``timeout_seconds`` (all users).

        The reaper advances these as a SKIP (``last_run_status="reaped"``, no
        failure-streak bump) — a dispatch that claimed the slot but never
        recorded an outcome (process killed mid-deploy, wedged connector) is
        indistinguishable from a graceful drain, so it must not light the failure
        banner. A genuinely hung-but-live dispatch is recorded as a real failure
        by the shorter per-dispatch timeout, well before this bound. Oldest-claim
        first, capped like the due poll.
        """
        threshold = now - timedelta(seconds=timeout_seconds)
        stmt = (
            select(DBSchedule)
            .where(
                DBSchedule.started_at.isnot(None),
                DBSchedule.started_at < threshold,
            )
            .order_by(DBSchedule.started_at.asc())
            .limit(min(limit, SchedulerConstants.DUE_BATCH_MAX))
        )
        result = await self.session.execute(stmt)
        db_rows = list(result.scalars().all())
        return [await ScheduleMapper.to_domain(r) for r in db_rows]


__all__ = ["ScheduleRepository"]
