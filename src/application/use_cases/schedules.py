"""CRUD use cases for workflow/sync schedules (v0.8.2).

Five small use cases in one file, following the established pattern: frozen
Command/Result objects, ``slots=True`` UseCase classes, ``async with uow``
transaction boundaries. Each is the single codepath behind every edge (CLI now,
HTTP API next, future MCP) — validation and target verification live HERE, never
in an adapter, so the surfaces stay thin.

All mutations key on the **target** (``workflow_id`` XOR ``sync_target``), not an
opaque schedule id, because that is the identity every edge already has (a
workflow detail page, a ``mixd sync schedule lastfm:plays`` invocation). The
one-schedule-per-target DB index guarantees the lookup is unambiguous.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from attrs import define, evolve

from src.application.services.schedule_timing import compute_next_run
from src.application.use_cases._shared.schedule_validators import (
    validate_iana_timezone,
)
from src.application.use_cases._shared.sync_targets import (
    sync_target_label,
    validate_sync_target,
)
from src.domain.entities.schedule import Schedule
from src.domain.exceptions import NotFoundError
from src.domain.repositories.interfaces import UnitOfWorkProtocol


def _require_single_target(workflow_id: UUID | None, sync_target: str | None) -> None:
    """Reject commands that don't name exactly one target (the exclusive arc)."""
    if (workflow_id is None) == (sync_target is None):
        raise ValueError(
            "schedule command must target exactly one of workflow_id or sync_target"
        )


def _with_next_run(schedule: Schedule, *, now: datetime) -> Schedule:
    """Return ``schedule`` with ``next_run_at`` recomputed from its own cadence.

    ``compute_next_run`` reads only the cadence fields already set on ``schedule``,
    so a single ``evolve`` suffices at every create/replace/enable site.
    """
    return evolve(schedule, next_run_at=compute_next_run(schedule, now=now))


async def _verify_target_exists(
    uow: UnitOfWorkProtocol,
    *,
    user_id: str,
    workflow_id: UUID | None,
    sync_target: str | None,
) -> None:
    """Confirm the target a schedule points at is real (else raise).

    A workflow target must resolve to an owned workflow
    (``get_workflow_by_id`` raises ``NotFoundError``); a sync target must be one
    of the schedulable identities (``validate_sync_target`` raises ``ValueError``).
    Prevents orphan schedules that could never dispatch.
    """
    if workflow_id is not None:
        await uow.get_workflow_repository().get_workflow_by_id(
            workflow_id, user_id=user_id
        )
    elif sync_target is not None:
        validate_sync_target(sync_target)


# ---------------------------------------------------------------------------
# Upsert (create-or-replace)
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class UpsertScheduleCommand:
    user_id: str
    workflow_id: UUID | None = None
    sync_target: str | None = None
    hour: int = 0
    minute: int = 0
    day_of_week: int | None = None
    timezone: str = "UTC"


@define(frozen=True, slots=True)
class UpsertScheduleResult:
    schedule: Schedule
    # True when a new schedule was created, False when an existing one was
    # replaced — lets the API answer 201 vs 200.
    created: bool


@define(slots=True)
class UpsertScheduleUseCase:
    """Create a schedule, or replace the existing one for the same target.

    Idempotent per target: re-running with new cadence overwrites in place
    rather than colliding with the one-per-target unique index. ``next_run_at``
    is always recomputed from the new cadence (never carried from the old row),
    and a replace preserves run history (``run_count`` / ``consecutive_failures``
    / ``last_run_*``) and the existing enabled/disabled status — reconfiguring
    the time of a disabled schedule does not silently re-enable it.

    Not race-safe under two *concurrent first creations* for the same target
    (both read ``existing=None``): the partial-unique index rejects the second
    INSERT, which surfaces as ``ScheduleAlreadyExistsError``. That's a rare
    double-submit with no corruption, so it's left to surface rather than guarded.
    """

    async def execute(
        self, command: UpsertScheduleCommand, uow: UnitOfWorkProtocol
    ) -> UpsertScheduleResult:
        _require_single_target(command.workflow_id, command.sync_target)
        timezone = validate_iana_timezone(command.timezone)

        async with uow:
            await _verify_target_exists(
                uow,
                user_id=command.user_id,
                workflow_id=command.workflow_id,
                sync_target=command.sync_target,
            )
            repo = uow.get_schedule_repository()
            existing = await repo.get_for_target(
                user_id=command.user_id,
                workflow_id=command.workflow_id,
                sync_target=command.sync_target,
            )
            now = datetime.now(UTC)

            if existing is not None:
                # Replace in place: new cadence, preserve identity + history +
                # status; recompute next_run_at forward from now.
                candidate = evolve(
                    existing,
                    hour=command.hour,
                    minute=command.minute,
                    day_of_week=command.day_of_week,
                    timezone=timezone,
                )
                replaced = _with_next_run(candidate, now=now)
                saved = await repo.update_schedule(replaced, user_id=command.user_id)
                return UpsertScheduleResult(schedule=saved, created=False)

            # Create fresh (enabled by default via the entity).
            candidate = Schedule(
                user_id=command.user_id,
                workflow_id=command.workflow_id,
                sync_target=command.sync_target,
                hour=command.hour,
                minute=command.minute,
                day_of_week=command.day_of_week,
                timezone=timezone,
            )
            saved = await repo.create(_with_next_run(candidate, now=now))
            return UpsertScheduleResult(schedule=saved, created=True)


# ---------------------------------------------------------------------------
# Toggle — enable or disable
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class ToggleScheduleCommand:
    user_id: str
    enabled: bool
    workflow_id: UUID | None = None
    sync_target: str | None = None


@define(frozen=True, slots=True)
class ToggleScheduleResult:
    schedule: Schedule


@define(slots=True)
class ToggleScheduleUseCase:
    """Enable or disable a schedule.

    Enabling recomputes ``next_run_at`` forward from now so a long-disabled
    schedule doesn't fire immediately for every missed window — it picks up at
    the next future occurrence. Disabling leaves ``next_run_at`` untouched (the
    poll filters on ``status='enabled'`` anyway).
    """

    async def execute(
        self, command: ToggleScheduleCommand, uow: UnitOfWorkProtocol
    ) -> ToggleScheduleResult:
        _require_single_target(command.workflow_id, command.sync_target)
        async with uow:
            repo = uow.get_schedule_repository()
            existing = await repo.get_for_target(
                user_id=command.user_id,
                workflow_id=command.workflow_id,
                sync_target=command.sync_target,
            )
            if existing is None:
                raise NotFoundError("No schedule found for target")

            if command.enabled:
                enabled = evolve(existing, status="enabled")
                updated = _with_next_run(enabled, now=datetime.now(UTC))
            else:
                updated = evolve(existing, status="disabled")

            saved = await repo.update_schedule(updated, user_id=command.user_id)
            return ToggleScheduleResult(schedule=saved)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class DeleteScheduleCommand:
    user_id: str
    workflow_id: UUID | None = None
    sync_target: str | None = None


@define(frozen=True, slots=True)
class DeleteScheduleResult:
    schedule_id: UUID


@define(slots=True)
class DeleteScheduleUseCase:
    async def execute(
        self, command: DeleteScheduleCommand, uow: UnitOfWorkProtocol
    ) -> DeleteScheduleResult:
        _require_single_target(command.workflow_id, command.sync_target)
        async with uow:
            repo = uow.get_schedule_repository()
            existing = await repo.get_for_target(
                user_id=command.user_id,
                workflow_id=command.workflow_id,
                sync_target=command.sync_target,
            )
            if existing is None:
                raise NotFoundError("No schedule found for target")
            await repo.delete_for_user(existing.id, user_id=command.user_id)
            return DeleteScheduleResult(schedule_id=existing.id)


# ---------------------------------------------------------------------------
# Get (single, by target)
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class GetScheduleCommand:
    user_id: str
    workflow_id: UUID | None = None
    sync_target: str | None = None


@define(frozen=True, slots=True)
class GetScheduleResult:
    # None when no schedule is configured for the target — the CLI prints an
    # empty state, the API answers 404. Returning None (not raising) keeps the
    # "does a schedule exist?" query cheap and exception-free.
    schedule: Schedule | None


@define(slots=True)
class GetScheduleUseCase:
    async def execute(
        self, command: GetScheduleCommand, uow: UnitOfWorkProtocol
    ) -> GetScheduleResult:
        _require_single_target(command.workflow_id, command.sync_target)
        async with uow:
            repo = uow.get_schedule_repository()
            schedule = await repo.get_for_target(
                user_id=command.user_id,
                workflow_id=command.workflow_id,
                sync_target=command.sync_target,
            )
            return GetScheduleResult(schedule=schedule)


# ---------------------------------------------------------------------------
# List (all of a user's schedules)
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class ListSchedulesCommand:
    user_id: str


@define(frozen=True, slots=True)
class ScheduleListEntry:
    """A schedule plus its resolved display label — the list read-model.

    ``target_label`` is the human name every surface (dashboard banner, CLI table)
    wants but the entity can't hold: a sync target's friendly name, or the
    workflow's name resolved at read time. Computed here, in the one list codepath,
    so no edge re-implements labeling.
    """

    schedule: Schedule
    target_label: str


@define(frozen=True, slots=True)
class ListSchedulesResult:
    entries: list[ScheduleListEntry]


@define(slots=True)
class ListSchedulesUseCase:
    async def execute(
        self, command: ListSchedulesCommand, uow: UnitOfWorkProtocol
    ) -> ListSchedulesResult:
        async with uow:
            schedules = await uow.get_schedule_repository().list_for_user(
                user_id=command.user_id
            )
            # Resolve workflow names only when a workflow schedule exists — a
            # sync-only user pays no extra query. Reuses the existing
            # list_workflows codepath rather than a parallel name lookup.
            names: dict[UUID, str] = {}
            if any(s.workflow_id is not None for s in schedules):
                workflows = await uow.get_workflow_repository().list_workflows(
                    user_id=command.user_id
                )
                names = {w.id: w.definition.name for w in workflows}

            return ListSchedulesResult(
                entries=[
                    ScheduleListEntry(schedule=s, target_label=_target_label(s, names))
                    for s in schedules
                ]
            )


def _target_label(schedule: Schedule, workflow_names: Mapping[UUID, str]) -> str:
    """Friendly target name: sync label, or the workflow's name (then its id)."""
    if schedule.sync_target is not None:
        return sync_target_label(schedule.sync_target)
    wid = schedule.workflow_id
    return workflow_names.get(wid, str(wid)) if wid is not None else "Workflow"
