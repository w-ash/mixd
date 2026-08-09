"""Import trigger and checkpoint status endpoints.

Each import endpoint pre-generates an operation_id, registers an SSE queue,
launches the import as a background task, and immediately returns the
operation_id so the client can subscribe to progress via SSE.
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from src.config import get_logger
from src.domain.repositories.play import RECENTLY_PLAYED_SCOPE
from src.interface.api.deps import (
    get_current_user_id,
    require_connector_connected,
    require_connector_scopes,
)
from src.interface.api.schemas.imports import (
    CheckpointStatusSchema,
    ExportLastfmLikesRequest,
    ImportLastfmHistoryRequest,
    ImportQueueResponse,
    ImportSpotifyLikesRequest,
    ImportSpotifyRecentRequest,
    OperationStartedResponse,
)
from src.interface.api.services.import_queue import (
    QueueEntry,
    cancel_pending,
    get_queue,
    receive_export_upload,
)
from src.interface.api.services.progress import OperationBoundEmitter
from src.interface.api.services.sse_operations import launch_sse_operation

logger = get_logger(__name__).bind(service="imports_api")

router = APIRouter(prefix="/imports", tags=["imports"])


# ---------------------------------------------------------------------------
# Import endpoints
# ---------------------------------------------------------------------------


@router.post("/lastfm/history")
async def import_lastfm_history(
    body: ImportLastfmHistoryRequest,
    user_id: str = Depends(get_current_user_id),
    _connected: None = Depends(require_connector_connected("lastfm")),
) -> OperationStartedResponse:
    """Trigger a Last.fm listening history import."""

    async def _import(emitter: OperationBoundEmitter) -> object:
        from src.application.use_cases.import_play_history import run_import

        return await run_import(
            user_id=user_id,
            service="lastfm",
            mode=body.mode,
            limit=body.limit,
            from_date=body.from_date,
            to_date=body.to_date,
            progress_emitter=emitter,
        )

    return await launch_sse_operation(
        user_id=user_id,
        operation_type="import_lastfm_history",
        coro_factory=_import,
    )


@router.post("/spotify/recent")
async def import_spotify_recent(
    body: ImportSpotifyRecentRequest,
    user_id: str = Depends(get_current_user_id),
    _scoped: None = Depends(
        require_connector_scopes("spotify", {RECENTLY_PLAYED_SCOPE})
    ),
) -> OperationStartedResponse:
    """Poll Spotify's recently-played API for new plays.

    Scope-gated rather than merely connection-gated: a grant minted before
    v0.10.1 still works for likes and playlists, so the generic connected check
    would let it through and fail later inside the operation.
    """

    async def _import(emitter: OperationBoundEmitter) -> object:
        from src.application.use_cases.import_play_history import run_import

        return await run_import(
            user_id=user_id,
            service="spotify",
            mode="recent",
            limit=body.limit,
            progress_emitter=emitter,
            force=body.force,
        )

    return await launch_sse_operation(
        user_id=user_id,
        operation_type="import_spotify_recent",
        coro_factory=_import,
    )


@router.post("/spotify/likes")
async def import_spotify_likes(
    body: ImportSpotifyLikesRequest,
    user_id: str = Depends(get_current_user_id),
    _connected: None = Depends(require_connector_connected("spotify")),
) -> OperationStartedResponse:
    """Trigger a Spotify liked tracks import."""

    async def _import(emitter: OperationBoundEmitter) -> object:
        from src.application.use_cases.sync_likes import run_spotify_likes_import

        return await run_spotify_likes_import(
            user_id=user_id,
            limit=body.limit,
            max_imports=body.max_imports,
            force=body.force,
            progress_emitter=emitter,
        )

    return await launch_sse_operation(
        user_id=user_id,
        operation_type="import_spotify_likes",
        coro_factory=_import,
    )


@router.post("/lastfm/likes")
async def export_lastfm_likes(
    body: ExportLastfmLikesRequest,
    user_id: str = Depends(get_current_user_id),
    _connected: None = Depends(require_connector_connected("lastfm")),
) -> OperationStartedResponse:
    """Trigger a Last.fm likes export (love tracks on Last.fm)."""

    async def _export(emitter: OperationBoundEmitter) -> object:
        from src.application.use_cases.sync_likes import run_lastfm_likes_export

        return await run_lastfm_likes_export(
            user_id=user_id,
            batch_size=body.batch_size,
            max_exports=body.max_exports,
            progress_emitter=emitter,
        )

    return await launch_sse_operation(
        user_id=user_id,
        operation_type="export_lastfm_likes",
        coro_factory=_export,
    )


def _queue_response(queue_id: str, entries: list[QueueEntry]) -> ImportQueueResponse:
    return ImportQueueResponse.model_validate(
        {"queue_id": queue_id, "entries": entries}, from_attributes=True
    )


@router.post("/spotify/history")
async def import_spotify_history(
    files: list[UploadFile],
    user_id: str = Depends(get_current_user_id),
) -> ImportQueueResponse:
    """Queue Spotify GDPR export JSON files for one sequential, unattended import.

    A single file is the degenerate one-entry queue. Guards, capped streaming,
    and queue start all live in ``receive_export_upload`` (409/422/413).
    """
    queue = await receive_export_upload(user_id, files)
    return _queue_response(queue.queue_id, queue.entries)


@router.get("/spotify/history/queue")
async def get_spotify_history_queue(
    user_id: str = Depends(get_current_user_id),
) -> ImportQueueResponse:
    """The user's current import queue, so a reloaded tab re-attaches to it."""
    queue = get_queue(user_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="No import queue")
    return _queue_response(queue.queue_id, queue.entries)


@router.delete("/spotify/history/queue")
async def cancel_spotify_history_queue(
    user_id: str = Depends(get_current_user_id),
) -> ImportQueueResponse:
    """Cancel the queue's not-yet-started entries; the running one finishes."""
    queue = cancel_pending(user_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="No import queue")
    return _queue_response(queue.queue_id, queue.entries)


# ---------------------------------------------------------------------------
# Checkpoint status
# ---------------------------------------------------------------------------


@router.get("/checkpoints")
async def get_checkpoints(
    user_id: str = Depends(get_current_user_id),
) -> list[CheckpointStatusSchema]:
    """Get sync checkpoint status for all known service/entity combinations."""
    from src.application.use_cases.sync_likes import get_all_checkpoint_statuses

    statuses = await get_all_checkpoint_statuses(user_id=user_id)
    return [
        CheckpointStatusSchema(
            service=s.service,
            entity_type=s.entity_type,
            last_sync_timestamp=s.last_sync_timestamp,
            has_previous_sync=s.has_previous_sync,
            local_count=s.local_count,
            remote_total=s.remote_total,
            last_polled_at=s.last_polled_at,
            effective_interval_seconds=s.effective_interval_seconds,
            poll_health=s.poll_health,
            possible_gap=s.possible_gap,
        )
        for s in statuses
    ]
