"""Import trigger and checkpoint status endpoints.

Each import endpoint pre-generates an operation_id, registers an SSE queue,
launches the import as a background task, and immediately returns the
operation_id so the client can subscribe to progress via SSE.
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: Coroutine type params in background task helper

import asyncio
from collections.abc import Callable, Coroutine
import os
from pathlib import Path
import tempfile
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, UploadFile

from src.application.services.progress_manager import get_progress_manager
from src.config import get_logger
from src.config.constants import SSEConstants
from src.interface.api.schemas.imports import (
    CheckpointStatusSchema,
    ExportLastfmLikesRequest,
    ImportLastfmHistoryRequest,
    ImportSpotifyLikesRequest,
    OperationStartedResponse,
)
from src.interface.api.services.progress import (
    OperationBoundEmitter,
    get_operation_registry,
)

logger = get_logger(__name__).bind(service="imports_api")

router = APIRouter(prefix="/imports", tags=["imports"])

# Maximum upload size for Spotify GDPR JSON files (100 MB)
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024

# Strong references prevent background tasks from being garbage-collected
_background_tasks: set[asyncio.Task[None]] = set()

# Tracks logically active operations (cleared before SSE grace period).
# Used for the 429 concurrency limit so finished-but-draining tasks don't
# block new imports.
_active_operations: set[str] = set()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_emitter(operation_id: str) -> OperationBoundEmitter:
    return OperationBoundEmitter(
        delegate=get_progress_manager(), operation_id=operation_id
    )


def _launch_background(
    operation_id: str,
    coro_factory: Callable[[], Coroutine[Any, Any, None]],
) -> None:
    """Launch a background coroutine and prevent GC of the task handle.

    Accepts a *factory* (zero-arg callable returning a coroutine) rather than
    a pre-created coroutine so tests can stub this without leaking unawaited
    coroutine objects.
    """
    task = asyncio.create_task(coro_factory(), name=f"import_{operation_id}")
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _run_operation(
    operation_id: str,
    coro: Coroutine[Any, Any, object],
) -> None:
    """Wrapper that runs a use-case coroutine and cleans up the SSE queue."""
    registry = get_operation_registry()
    _active_operations.add(operation_id)
    try:
        await coro
    except Exception:
        logger.opt(exception=True).error(
            "Import operation failed", operation_id=operation_id
        )
    finally:
        # Mark operation as no longer active before the grace period so new
        # imports aren't blocked by the 429 limit during SSE drain.
        _active_operations.discard(operation_id)
        # Give SSE clients time to read final events before cleanup
        await asyncio.sleep(SSEConstants.GRACE_PERIOD_SECONDS)
        await registry.unregister(operation_id)


async def _prepare_operation() -> tuple[str, OperationBoundEmitter]:
    """Pre-generate operation_id, register SSE queue, build emitter.

    Raises HTTPException(429) if the concurrent operation limit is reached,
    checked *before* allocating any resources.
    """
    if len(_active_operations) >= SSEConstants.MAX_CONCURRENT_OPERATIONS:
        raise HTTPException(
            status_code=429,
            detail="Too many concurrent operations. Please wait for a running import to finish.",
            headers={"Retry-After": "30"},
        )
    operation_id = str(uuid4())
    registry = get_operation_registry()
    await registry.register(operation_id)
    emitter = _make_emitter(operation_id)
    return operation_id, emitter


# ---------------------------------------------------------------------------
# Import endpoints
# ---------------------------------------------------------------------------


@router.post("/lastfm/history")
async def import_lastfm_history(
    body: ImportLastfmHistoryRequest,
) -> OperationStartedResponse:
    """Trigger a Last.fm listening history import."""
    operation_id, emitter = await _prepare_operation()

    async def _import() -> None:
        from src.application.use_cases.import_play_history import run_import

        await run_import(
            service="lastfm",
            mode=body.mode,
            limit=body.limit,
            from_date=body.from_date,
            to_date=body.to_date,
            progress_emitter=emitter,
        )

    _launch_background(operation_id, lambda: _run_operation(operation_id, _import()))
    return OperationStartedResponse(operation_id=operation_id)


@router.post("/spotify/likes")
async def import_spotify_likes(
    body: ImportSpotifyLikesRequest,
) -> OperationStartedResponse:
    """Trigger a Spotify liked tracks import."""
    operation_id, emitter = await _prepare_operation()

    async def _import() -> None:
        from src.application.use_cases.sync_likes import run_spotify_likes_import

        await run_spotify_likes_import(
            user_id="default",
            limit=body.limit,
            max_imports=body.max_imports,
            progress_emitter=emitter,
        )

    _launch_background(operation_id, lambda: _run_operation(operation_id, _import()))
    return OperationStartedResponse(operation_id=operation_id)


@router.post("/lastfm/likes")
async def export_lastfm_likes(
    body: ExportLastfmLikesRequest,
) -> OperationStartedResponse:
    """Trigger a Last.fm likes export (love tracks on Last.fm)."""
    operation_id, emitter = await _prepare_operation()

    async def _import() -> None:
        from src.application.use_cases.sync_likes import run_lastfm_likes_export

        await run_lastfm_likes_export(
            user_id="default",
            batch_size=body.batch_size,
            max_exports=body.max_exports,
            progress_emitter=emitter,
        )

    _launch_background(operation_id, lambda: _run_operation(operation_id, _import()))
    return OperationStartedResponse(operation_id=operation_id)


@router.post("/spotify/history")
async def import_spotify_history(file: UploadFile) -> OperationStartedResponse:
    """Import Spotify listening history from a GDPR data export JSON file."""
    if file.size and file.size > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({file.size} bytes). Maximum is {_MAX_UPLOAD_BYTES} bytes.",
        )

    # Save uploaded file to temp location for background processing.
    # Using os.* instead of pathlib for async-safe file I/O (ASYNC240).
    fd, temp_name = tempfile.mkstemp(suffix=".json")
    try:
        content = await file.read()
        os.write(fd, content)
        os.close(fd)
    except Exception:
        os.close(fd)
        os.unlink(temp_name)  # noqa: PTH108 — os.unlink is async-safe, pathlib is not (ASYNC240)
        raise

    temp_path = Path(temp_name)
    operation_id, emitter = await _prepare_operation()

    async def _import() -> None:
        from src.application.use_cases.import_play_history import run_import

        try:
            await run_import(
                service="spotify",
                mode="file",
                file_path=temp_path,
                progress_emitter=emitter,
            )
        finally:
            os.unlink(temp_name)  # noqa: PTH108 — os.unlink is async-safe, pathlib is not (ASYNC240)

    _launch_background(operation_id, lambda: _run_operation(operation_id, _import()))
    return OperationStartedResponse(operation_id=operation_id)


# ---------------------------------------------------------------------------
# Checkpoint status
# ---------------------------------------------------------------------------


@router.get("/checkpoints")
async def get_checkpoints() -> list[CheckpointStatusSchema]:
    """Get sync checkpoint status for all known service/entity combinations."""
    from src.application.use_cases.sync_likes import get_sync_checkpoint_status

    results: list[CheckpointStatusSchema] = []
    for service, entity_type in (
        ("spotify", "likes"),
        ("lastfm", "likes"),
        ("lastfm", "plays"),
        ("spotify", "plays"),
    ):
        status = await get_sync_checkpoint_status(
            service, entity_type  # pyright: ignore[reportArgumentType]
        )
        results.append(
            CheckpointStatusSchema(
                service=status.service,
                entity_type=status.entity_type,
                last_sync_timestamp=status.last_sync_timestamp,
                has_previous_sync=status.has_previous_sync,
            )
        )

    return results
