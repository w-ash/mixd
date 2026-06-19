"""Schedule repository protocol.

Split from the former monolithic ``interfaces.py``.
"""

from collections.abc import Awaitable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from src.domain.entities.schedule import Schedule


class ScheduleRepositoryProtocol(Protocol):
    """Repository interface for ``Schedule`` persistence (v0.8.2 scheduling).

    Two access patterns: per-user **CRUD** (the ``schedules`` table has no RLS,
    so every CRUD method filters by ``user_id``) and the cross-tenant **system
    hot-path** the scheduler polls (``find_due_schedules`` / ``mark_schedule_*``
    / ``list_stuck_started`` take no ``user_id`` by design). The optimistic
    claim in ``mark_schedule_started`` is what makes concurrent polling safe.
    """

    def create(self, schedule: Schedule) -> Awaitable[Schedule]:
        """Insert a new schedule. Maps a one-per-target unique violation to
        ``ScheduleAlreadyExistsError``.
        """
        ...

    def update_schedule(
        self, schedule: Schedule, *, user_id: str
    ) -> Awaitable[Schedule]:
        """Overwrite the mutable fields of an existing schedule (user-scoped).

        Raises ``NotFoundError`` if no row matches ``(id, user_id)``.
        """
        ...

    def delete_for_user(self, schedule_id: UUID, *, user_id: str) -> Awaitable[bool]:
        """Delete one schedule. Returns ``True`` if a row was removed."""
        ...

    def get_by_id_for_user(
        self, schedule_id: UUID, *, user_id: str
    ) -> Awaitable[Schedule | None]:
        """Return the schedule if it exists AND is owned by ``user_id``."""
        ...

    def get_for_target(
        self,
        *,
        user_id: str,
        workflow_id: UUID | None = None,
        sync_target: str | None = None,
    ) -> Awaitable[Schedule | None]:
        """Return this user's schedule for one target (exactly one arg), or None."""
        ...

    def list_for_user(self, *, user_id: str) -> Awaitable[list[Schedule]]:
        """List all of a user's schedules, newest-first."""
        ...

    def try_acquire_poll_lock(self, key: int = ...) -> Awaitable[bool]:
        """Try to win this tick's transaction-level poll lock. ``True`` iff held.

        Taken at the top of the poll transaction so only one replica scans per tick;
        auto-releases when that transaction ends. Not a correctness mechanism — the
        atomic ``mark_schedule_started`` claim prevents double-dispatch.
        """
        ...

    def find_due_schedules(
        self, now: datetime, *, limit: int = ...
    ) -> Awaitable[list[Schedule]]:
        """Enabled, unclaimed schedules whose fire time has arrived (all users)."""
        ...

    def get_by_id(self, id_: UUID) -> Awaitable[Schedule]:
        """Return a schedule by id, cross-tenant (no ``user_id`` filter).

        System hot-path read: the scheduler re-reads a claimed row's current
        cadence before the terminal write. Raises ``NotFoundError`` if the row is
        gone. CRUD callers use the user-scoped ``get_by_id_for_user`` instead.
        """
        ...

    def mark_schedule_started(
        self,
        schedule_id: UUID,
        *,
        expected_next_run_at: datetime,
        now: datetime,
    ) -> Awaitable[bool]:
        """Optimistically claim a due schedule. ``True`` iff this caller won."""
        ...

    def mark_schedule_completed(
        self,
        schedule_id: UUID,
        *,
        next_run_at: datetime,
        last_run_at: datetime,
        last_run_status: str,
        last_run_id: UUID | None = None,
    ) -> Awaitable[bool]:
        """Record a successful fire, reset failures, advance, release the claim."""
        ...

    def mark_schedule_skipped(
        self,
        schedule_id: UUID,
        *,
        next_run_at: datetime,
        last_run_at: datetime,
        last_run_status: str,
        reset_failures: bool = False,
    ) -> Awaitable[bool]:
        """Release the claim and advance without counting a run (skip).

        ``reset_failures=True`` also clears the failure streak (used for
        ``skipped_already_running``, where the workflow is demonstrably healthy).
        """
        ...

    def mark_schedule_failed(
        self,
        schedule_id: UUID,
        *,
        next_run_at: datetime,
        last_run_at: datetime,
        last_error: str,
        last_run_status: str = "failed",
    ) -> Awaitable[bool]:
        """Record a failed fire, increment failures, advance, release the claim."""
        ...

    def mark_schedule_disabled(
        self, schedule_id: UUID, *, last_error: str
    ) -> Awaitable[bool]:
        """Disable a claimed schedule and release its claim (orphaned target)."""
        ...

    def list_stuck_started(
        self,
        timeout_seconds: int,
        *,
        now: datetime,
        limit: int = ...,
    ) -> Awaitable[list[Schedule]]:
        """Schedules claimed longer ago than ``timeout_seconds`` (all users)."""
        ...
