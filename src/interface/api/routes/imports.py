"""Import trigger and checkpoint status endpoints.

Each import endpoint pre-generates an operation_id, registers an SSE queue,
launches the import as a background task, and immediately returns the
operation_id so the client can subscribe to progress via SSE.
"""

import os
from pathlib import Path
import shutil
import tempfile

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from src.config import get_logger
from src.config.constants import BusinessLimits
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
    IMPORT_QUEUE_TMPDIR_PREFIX,
    QueueEntry,
    cancel_pending,
    get_queue,
    raise_if_queue_active,
    start_queue,
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


async def _stream_uploads_to_queue_dir(
    files: list[UploadFile], tmpdir: Path
) -> list[QueueEntry]:
    """Stream every upload into ``tmpdir`` under server-chosen names.

    Files land as ``{position:03d}.json`` — client filenames are display data,
    never paths. 64KB chunks keep memory flat, and both caps are enforced on
    real bytes as they arrive, regardless of what Content-Length claimed: a
    per-file breach of ``MAX_UPLOAD_BYTES`` or a running-total breach of
    ``MAX_QUEUED_UPLOAD_BYTES`` removes the whole directory and 413s, so a
    rejected request leaves nothing on disk.
    """
    total_bytes = 0
    entries: list[QueueEntry] = []
    try:
        for position, file in enumerate(files):
            path = tmpdir / f"{position:03d}.json"
            total_bytes = await _stream_upload_capped(file, path, total_bytes)
            entries.append(
                QueueEntry(
                    filename=file.filename or f"file-{position + 1}.json",
                    position=position,
                    path=path,
                )
            )
    except BaseException:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise
    return entries


def _raise_if_over_upload_caps(file_bytes: int, total_bytes: int) -> None:
    if file_bytes > BusinessLimits.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (>{BusinessLimits.MAX_UPLOAD_BYTES} bytes). Maximum is {BusinessLimits.MAX_UPLOAD_BYTES} bytes per file.",
        )
    if total_bytes > BusinessLimits.MAX_QUEUED_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Upload too large (>{BusinessLimits.MAX_QUEUED_UPLOAD_BYTES} bytes total). Maximum is {BusinessLimits.MAX_QUEUED_UPLOAD_BYTES} bytes per queue.",
        )


async def _stream_upload_capped(file: UploadFile, path: Path, total_bytes: int) -> int:
    """Stream one upload to ``path``; return the updated queue-wide byte total.

    Caps are checked before each write, so no byte past either limit lands.
    """
    # os.* rather than pathlib for async-safe file I/O (ASYNC240).
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        file_bytes = 0
        while chunk := await file.read(64 * 1024):
            file_bytes += len(chunk)
            total_bytes += len(chunk)
            _raise_if_over_upload_caps(file_bytes, total_bytes)
            os.write(fd, chunk)
    finally:
        os.close(fd)
    return total_bytes


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

    A single file is the degenerate one-entry queue. Declared sizes are only a
    cheap early rejection — the streaming writer re-enforces both caps on real
    bytes.
    """
    raise_if_queue_active(user_id)
    if len(files) > BusinessLimits.MAX_QUEUE_ENTRIES:
        raise HTTPException(
            status_code=422,
            detail=f"Too many files ({len(files)}). Maximum is {BusinessLimits.MAX_QUEUE_ENTRIES} per queue.",
        )
    declared_total = sum(file.size or 0 for file in files)
    if declared_total > BusinessLimits.MAX_QUEUED_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Upload too large ({declared_total} bytes total). Maximum is {BusinessLimits.MAX_QUEUED_UPLOAD_BYTES} bytes per queue.",
        )

    tmpdir = Path(tempfile.mkdtemp(prefix=IMPORT_QUEUE_TMPDIR_PREFIX))
    entries = await _stream_uploads_to_queue_dir(files, tmpdir)
    try:
        queue = start_queue(user_id=user_id, tmpdir=tmpdir, entries=entries)
    except BaseException:
        # A refused start (slot 429, or losing the raced 409 re-check) must not
        # strand the streamed bytes — the startup sweep only runs on restart,
        # and autostop is off, so this process may live for weeks.
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise
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
