"""Import trigger and checkpoint status endpoints.

Each import endpoint pre-generates an operation_id, registers an SSE queue,
launches the import as a background task, and immediately returns the
operation_id so the client can subscribe to progress via SSE.
"""

import os
from pathlib import Path
import tempfile

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from src.config import get_logger
from src.config.constants import BusinessLimits
from src.interface.api.deps import get_current_user_id
from src.interface.api.schemas.imports import (
    CheckpointStatusSchema,
    ExportLastfmLikesRequest,
    ImportLastfmHistoryRequest,
    ImportSpotifyLikesRequest,
    OperationStartedResponse,
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
) -> OperationStartedResponse:
    """Trigger a Last.fm listening history import."""

    async def _import(emitter: OperationBoundEmitter) -> None:
        from src.application.use_cases.import_play_history import run_import

        await run_import(
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


@router.post("/spotify/likes")
async def import_spotify_likes(
    body: ImportSpotifyLikesRequest,
    user_id: str = Depends(get_current_user_id),
) -> OperationStartedResponse:
    """Trigger a Spotify liked tracks import."""

    async def _import(emitter: OperationBoundEmitter) -> None:
        from src.application.use_cases.sync_likes import run_spotify_likes_import

        await run_spotify_likes_import(
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
) -> OperationStartedResponse:
    """Trigger a Last.fm likes export (love tracks on Last.fm)."""

    async def _export(emitter: OperationBoundEmitter) -> None:
        from src.application.use_cases.sync_likes import run_lastfm_likes_export

        await run_lastfm_likes_export(
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


@router.post("/spotify/history")
async def import_spotify_history(
    file: UploadFile,
    user_id: str = Depends(get_current_user_id),
) -> OperationStartedResponse:
    """Import Spotify listening history from a GDPR data export JSON file."""
    if file.size and file.size > BusinessLimits.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({file.size} bytes). Maximum is {BusinessLimits.MAX_UPLOAD_BYTES} bytes.",
        )

    # Save uploaded file to temp location with server-side byte counting.
    # Streaming 64KB chunks avoids loading the entire file into memory and
    # enforces the size limit regardless of what Content-Length claims.
    # Using os.* instead of pathlib for async-safe file I/O (ASYNC240).
    fd, temp_name = tempfile.mkstemp(suffix=".json")
    oversized = False
    try:
        bytes_written = 0
        while chunk := await file.read(64 * 1024):
            bytes_written += len(chunk)
            if bytes_written > BusinessLimits.MAX_UPLOAD_BYTES:
                oversized = True
                break
            os.write(fd, chunk)
    except BaseException:
        os.close(fd)
        os.unlink(temp_name)  # noqa: PTH108 — os.unlink is async-safe, pathlib is not (ASYNC240)
        raise
    else:
        os.close(fd)

    if oversized:
        os.unlink(temp_name)  # noqa: PTH108 — os.unlink is async-safe, pathlib is not (ASYNC240)
        raise HTTPException(
            status_code=413,
            detail=f"File too large (>{BusinessLimits.MAX_UPLOAD_BYTES} bytes). Maximum is {BusinessLimits.MAX_UPLOAD_BYTES} bytes.",
        )

    temp_path = Path(temp_name)

    async def _import(emitter: OperationBoundEmitter) -> None:
        from src.application.use_cases.import_play_history import run_import

        try:
            await run_import(
                user_id=user_id,
                service="spotify",
                mode="file",
                file_path=temp_path,
                progress_emitter=emitter,
            )
        finally:
            os.unlink(temp_name)  # noqa: PTH108 — os.unlink is async-safe, pathlib is not (ASYNC240)

    return await launch_sse_operation(
        user_id=user_id,
        operation_type="import_spotify_history",
        coro_factory=_import,
    )


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
        )
        for s in statuses
    ]
