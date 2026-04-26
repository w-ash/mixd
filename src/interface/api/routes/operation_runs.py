"""OperationRun audit-log endpoints (v0.7.7).

Distinct from ``operations.py`` (which owns the in-memory SSE registry).
This router surfaces the persisted ``operation_runs`` table — one row per
long-running SSE operation kicked off by the user, with summary list and
full detail (including issues) for post-run inspection.
"""

from collections.abc import Sequence
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from src.application.runner import execute_use_case
from src.application.use_cases.get_operation_run import (
    GetOperationRunCommand,
    GetOperationRunUseCase,
)
from src.application.use_cases.list_operation_runs import (
    ListOperationRunsCommand,
    ListOperationRunsUseCase,
)
from src.interface.api.deps import get_current_user_id
from src.interface.api.schemas.operation_runs import (
    OperationRunDetailSchema,
    OperationRunListResponse,
    OperationRunSummarySchema,
)

router = APIRouter(prefix="/operation-runs", tags=["operation-runs"])


# Default filter for the Import History UI: surfaces user-triggered
# import / sync / apply runs but excludes infrastructure-style flows.
# Pass ``type_filter=all`` to see every persisted run.
_IMPORT_LIKE_TYPES: Sequence[str] = (
    "import_lastfm_history",
    "import_spotify_likes",
    "export_lastfm_likes",
    "import_spotify_history",
    "import_connector_playlists",
    "apply_assignments_bulk",
)


@router.get("")
async def list_operation_runs(
    user_id: Annotated[str, Depends(get_current_user_id)],
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None),
    type_filter: Annotated[str, Query(alias="type")] = "imports",
) -> OperationRunListResponse:
    """List the user's audit-log rows newest-first, keyset-paginated.

    ``type=imports`` (default) restricts to import/sync/apply runs that
    surface in the Import History UI. ``type=all`` returns every row.
    """
    operation_types: Sequence[str] | None = (
        list(_IMPORT_LIKE_TYPES) if type_filter != "all" else None
    )

    command = ListOperationRunsCommand(
        user_id=user_id,
        limit=limit,
        encoded_cursor=cursor,
        operation_types=operation_types,
    )
    result = await execute_use_case(
        lambda uow: ListOperationRunsUseCase().execute(command, uow),
        user_id=user_id,
    )
    return OperationRunListResponse(
        data=[
            OperationRunSummarySchema(
                id=r.id,
                operation_type=r.operation_type,
                started_at=r.started_at,
                ended_at=r.ended_at,
                status=r.status,
                counts=dict(r.counts),
                issue_count=len(r.issues),
            )
            for r in result.runs
        ],
        limit=limit,
        next_cursor=result.next_cursor,
    )


@router.get("/{run_id}")
async def get_operation_run(
    run_id: UUID,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> OperationRunDetailSchema:
    """Get one run with the full issues payload.

    Returns 404 for both not-found AND not-owner — deliberately uniform
    so the response can't be used to infer the existence of another
    user's run.
    """
    command = GetOperationRunCommand(user_id=user_id, run_id=run_id)
    run = await execute_use_case(
        lambda uow: GetOperationRunUseCase().execute(command, uow),
        user_id=user_id,
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Operation run not found")
    return OperationRunDetailSchema(
        id=run.id,
        operation_type=run.operation_type,
        started_at=run.started_at,
        ended_at=run.ended_at,
        status=run.status,
        counts=dict(run.counts),
        issues=list(run.issues),
    )
