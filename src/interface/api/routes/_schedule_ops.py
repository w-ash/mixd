"""Shared request→use-case glue for the schedule endpoints (v0.8.2).

The sync-schedule router (``routes/schedules.py``) and the workflow-schedule
routes (``routes/workflows.py``) expose the identical create/read/toggle/delete
surface over the same use cases, differing ONLY in which target identity they
carry (``sync_target`` vs ``workflow_id``). These four coroutines hold that one
shared body so each router stays a thin set of decorated path declarations that
only supply the target — no business logic duplicated across the two files.
"""

from uuid import UUID

from fastapi import Response

from src.application.runner import execute_use_case
from src.application.use_cases.schedules import (
    DeleteScheduleCommand,
    DeleteScheduleUseCase,
    GetScheduleCommand,
    GetScheduleUseCase,
    ToggleScheduleCommand,
    ToggleScheduleUseCase,
    UpsertScheduleCommand,
    UpsertScheduleUseCase,
)
from src.domain.exceptions import NotFoundError
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.interface.api.schemas.schedules import (
    ScheduleResponse,
    ScheduleUpsertRequest,
)


async def upsert_schedule(
    *,
    user_id: str,
    body: ScheduleUpsertRequest,
    response: Response,
    workflow_id: UUID | None = None,
    sync_target: str | None = None,
) -> ScheduleResponse:
    """Create or replace one target's schedule (201 created / 200 replaced)."""
    command = UpsertScheduleCommand(
        user_id=user_id,
        workflow_id=workflow_id,
        sync_target=sync_target,
        hour=body.hour,
        minute=body.minute,
        day_of_week=body.day_of_week,
        timezone=body.timezone,
    )
    result = await execute_use_case(
        lambda uow: UpsertScheduleUseCase().execute(command, uow),
        user_id=user_id,
    )
    response.status_code = 201 if result.created else 200
    return ScheduleResponse.model_validate(result.schedule)


async def get_schedule(
    *,
    user_id: str,
    not_found_message: str,
    workflow_id: UUID | None = None,
    sync_target: str | None = None,
) -> ScheduleResponse:
    """Return a target's schedule, or raise ``NotFoundError`` (→ 404) if none."""

    async def _fetch(uow: UnitOfWorkProtocol) -> ScheduleResponse:
        result = await GetScheduleUseCase().execute(
            GetScheduleCommand(
                user_id=user_id, workflow_id=workflow_id, sync_target=sync_target
            ),
            uow,
        )
        if result.schedule is None:
            raise NotFoundError(not_found_message)
        return ScheduleResponse.model_validate(result.schedule)

    return await execute_use_case(_fetch, user_id=user_id)


async def toggle_schedule(
    *,
    user_id: str,
    enabled: bool,
    workflow_id: UUID | None = None,
    sync_target: str | None = None,
) -> ScheduleResponse:
    """Enable or disable a target's schedule (preserves run history)."""
    result = await execute_use_case(
        lambda uow: ToggleScheduleUseCase().execute(
            ToggleScheduleCommand(
                user_id=user_id,
                enabled=enabled,
                workflow_id=workflow_id,
                sync_target=sync_target,
            ),
            uow,
        ),
        user_id=user_id,
    )
    return ScheduleResponse.model_validate(result.schedule)


async def delete_schedule(
    *,
    user_id: str,
    workflow_id: UUID | None = None,
    sync_target: str | None = None,
) -> Response:
    """Delete a target's schedule (404 if none). Run history is preserved."""
    await execute_use_case(
        lambda uow: DeleteScheduleUseCase().execute(
            DeleteScheduleCommand(
                user_id=user_id, workflow_id=workflow_id, sync_target=sync_target
            ),
            uow,
        ),
        user_id=user_id,
    )
    return Response(status_code=204)
