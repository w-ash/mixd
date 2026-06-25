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
from src.application.services.progress_broker import get_progress_broker
from src.application.use_cases.get_operation_run import (
    GetOperationRunCommand,
    GetOperationRunUseCase,
)
from src.application.use_cases.import_connector_playlist_as_canonical import (
    run_import_connector_playlists_as_canonical,
    to_operation_result,
)
from src.application.use_cases.list_operation_runs import (
    ListOperationRunsCommand,
    ListOperationRunsUseCase,
)
from src.domain.entities.operation_run import OperationStatus
from src.domain.entities.playlist_link import SyncDirection
from src.domain.entities.shared import ConnectorPlaylistIdentifier
from src.interface.api.deps import get_current_user_id
from src.interface.api.schemas.imports import OperationStartedResponse
from src.interface.api.schemas.operation_runs import (
    OperationRunDetailSchema,
    OperationRunListResponse,
    OperationRunSummarySchema,
)
from src.interface.api.services.progress import OperationBoundEmitter
from src.interface.api.services.sse_operations import launch_sse_operation

# Only import runs carry the connector config + per-playlist issues that make a
# targeted "retry the failed ones" reconstructable from the audit row alone.
_RETRYABLE_OPERATION_TYPE = "import_connector_playlists"

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
    status: Annotated[OperationStatus | None, Query()] = None,
) -> OperationRunListResponse:
    """List the user's audit-log rows newest-first, keyset-paginated.

    ``type=imports`` (default) restricts to import/sync/apply runs that
    surface in the Import History UI. ``type=all`` returns every row.
    ``status=running`` powers operation-awareness: the in-flight rows the
    frontend re-attaches to (each carries its ``operation_id`` SSE handle).
    """
    operation_types: Sequence[str] | None = (
        list(_IMPORT_LIKE_TYPES) if type_filter != "all" else None
    )

    command = ListOperationRunsCommand(
        user_id=user_id,
        limit=limit,
        encoded_cursor=cursor,
        operation_types=operation_types,
        status=status,
    )
    result = await execute_use_case(
        lambda uow: ListOperationRunsUseCase().execute(command, uow),
        user_id=user_id,
    )
    return OperationRunListResponse(
        data=[
            OperationRunSummarySchema(
                id=r.id,
                operation_id=r.operation_id,
                operation_type=r.operation_type,
                started_at=r.started_at,
                ended_at=r.ended_at,
                status=r.status,
                counts=dict(r.counts),
                issue_count=len(r.issues),
                retryable=r.is_retryable,
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
        operation_id=run.operation_id,
        operation_type=run.operation_type,
        started_at=run.started_at,
        ended_at=run.ended_at,
        status=run.status,
        counts=dict(run.counts),
        issues=list(run.issues),
        retryable=run.is_retryable,
    )


@router.post("/{run_id}/retry-failed", status_code=202)
async def retry_failed_operation(
    run_id: UUID,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> OperationStartedResponse:
    """Re-run only the failed items of a terminal import run.

    Server-reconstructed: the failed connector-playlist ids come from the run's
    ``issues`` and the connector + direction from its ``request_params``. The
    owner is taken from auth, never from stored data. Re-invokes the *same*
    import use case with the failed subset as a fresh ``OperationRun`` — no new
    orchestration. Returns 409 when there's nothing retryable (so the caller can
    fall back to "View log").
    """
    command = GetOperationRunCommand(user_id=user_id, run_id=run_id)
    run = await execute_use_case(
        lambda uow: GetOperationRunUseCase().execute(command, uow),
        user_id=user_id,
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Operation run not found")
    if run.status == "running":
        raise HTTPException(status_code=409, detail="Operation is still running")
    # The domain entity owns "what is retryable, reconstructed from this row" —
    # status, type, connector config, and a non-empty failed subset (see
    # OperationRun.is_retryable / failed_connector_identifiers).
    if not run.is_retryable:
        raise HTTPException(status_code=409, detail="Nothing to retry for this run")

    connector = str(run.request_params["connector_name"])
    direction = str(run.request_params["sync_direction"])
    failed_ids = run.failed_connector_identifiers

    async def _retry(emitter: OperationBoundEmitter) -> object:
        result = await run_import_connector_playlists_as_canonical(
            user_id=user_id,
            connector_name=connector,
            connector_playlist_identifiers=[
                ConnectorPlaylistIdentifier(x) for x in failed_ids
            ],
            sync_direction=SyncDirection(direction),
            progress_emitter=emitter,
            progress_broker=get_progress_broker(),
            parent_operation_id=emitter.operation_id,
            run_id=emitter.run_id,
        )
        return to_operation_result(result)

    return await launch_sse_operation(
        user_id=user_id,
        operation_type=_RETRYABLE_OPERATION_TYPE,
        coro_factory=_retry,
        # Persist config so a retry-of-a-retry stays reconstructable.
        request_params={
            "connector_name": connector,
            "sync_direction": direction,
        },
    )
