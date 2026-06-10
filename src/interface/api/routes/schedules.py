"""Schedule endpoints for background syncs + the cross-target list (v0.8.2).

Sync schedules are keyed by their ``service:entity`` target id (e.g.
``lastfm:plays``); workflow schedules live on the workflows router. Every route
is user-scoped through the use cases — a cross-user target simply has no schedule
for *this* user, so reads 404 and the poll never leaks. The scheduler's
``find_due_schedules`` is deliberately never reachable from any route.

The create/read/toggle/delete handlers delegate to ``routes/_schedule_ops.py``,
which holds the body shared with the workflow-schedule routes (they differ only
in the target identity passed). The cross-target list lives here only.
"""

from fastapi import APIRouter, Depends, Response

from src.application.runner import execute_use_case
from src.application.use_cases.schedules import (
    ListSchedulesCommand,
    ListSchedulesUseCase,
)
from src.interface.api.deps import get_current_user_id
from src.interface.api.routes._schedule_ops import (
    delete_schedule,
    get_schedule,
    toggle_schedule,
    upsert_schedule,
)
from src.interface.api.schemas.schedules import (
    ScheduleListItem,
    ScheduleListResponse,
    ScheduleResponse,
    ScheduleToggleRequest,
    ScheduleUpsertRequest,
)

router = APIRouter(tags=["schedules"])


@router.get("/schedules")
async def list_schedules(
    user_id: str = Depends(get_current_user_id),
) -> ScheduleListResponse:
    """List all of the current user's schedules (workflow + sync)."""
    result = await execute_use_case(
        lambda uow: ListSchedulesUseCase().execute(
            ListSchedulesCommand(user_id=user_id), uow
        ),
        user_id=user_id,
    )
    return ScheduleListResponse(
        data=[
            ScheduleListItem.from_response(
                ScheduleResponse.model_validate(e.schedule),
                target_label=e.target_label,
            )
            for e in result.entries
        ]
    )


@router.put("/sync/schedules/{target_id}")
async def upsert_sync_schedule(
    target_id: str,
    body: ScheduleUpsertRequest,
    response: Response,
    user_id: str = Depends(get_current_user_id),
) -> ScheduleResponse:
    """Create or replace the schedule for a sync target (201 created / 200 replaced).

    An unschedulable ``target_id`` is rejected by the use case's
    ``validate_sync_target`` (→ ``ValueError`` → 400).
    """
    return await upsert_schedule(
        user_id=user_id, body=body, response=response, sync_target=target_id
    )


@router.get("/sync/schedules/{target_id}")
async def get_sync_schedule(
    target_id: str,
    user_id: str = Depends(get_current_user_id),
) -> ScheduleResponse:
    """Return the schedule for a sync target, or 404 if none is configured."""
    return await get_schedule(
        user_id=user_id,
        not_found_message=f"No schedule for sync target {target_id!r}",
        sync_target=target_id,
    )


@router.patch("/sync/schedules/{target_id}")
async def toggle_sync_schedule(
    target_id: str,
    body: ScheduleToggleRequest,
    user_id: str = Depends(get_current_user_id),
) -> ScheduleResponse:
    """Enable or disable a sync schedule (preserves its run history)."""
    return await toggle_schedule(
        user_id=user_id, enabled=body.enabled, sync_target=target_id
    )


@router.delete("/sync/schedules/{target_id}", status_code=204)
async def delete_sync_schedule(
    target_id: str,
    user_id: str = Depends(get_current_user_id),
) -> Response:
    """Delete a sync schedule (404 if none). Run history is preserved (SET NULL)."""
    return await delete_schedule(user_id=user_id, sync_target=target_id)
