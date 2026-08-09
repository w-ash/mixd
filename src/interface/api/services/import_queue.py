"""Sequential drain of a multi-file GDPR export upload (interface concern only).

One queue per user, held in process memory like the SSE registry's
``_active_operations`` — never the database: a queued file has no
``operation_runs`` row until it starts, and the queue's durability is bounded
by the temp files it points at (both die together on a machine restart, which
is honest rather than half-durable).

The sequencer holds exactly one ``SSEConstants.MAX_CONCURRENT_OPERATIONS``
slot for the whole drain (``acquire_operation_slot`` on its own token) and
launches each entry via the shared ``launch_sse_operation`` with
``occupies_slot=False`` — sequential by design (v0.10.2.4 measured ~80% of
import time in the shared write path, so parallel files buy contention, not
throughput) and one-slot so a Last.fm import or workflow can still run while
the export drains.
"""

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
from pathlib import Path
import shutil
import tempfile
from typing import Final
from uuid import uuid4

from attrs import define
from fastapi import HTTPException

from src.config import get_logger
from src.domain.entities.operation_run import OperationStatus
from src.interface.api.schemas.imports import QueueEntryStatus
from src.interface.api.services.background import launch_background
from src.interface.api.services.progress import OperationBoundEmitter
from src.interface.api.services.sse_operations import (
    acquire_operation_slot,
    launch_sse_operation,
    release_operation_slot,
)

logger = get_logger(__name__).bind(service="import_queue")

# Queue temp directories are created with this prefix so a startup sweep can
# tell an orphaned queue dir (previous process) from any other tempdir content.
IMPORT_QUEUE_TMPDIR_PREFIX: Final = "mixd-import-queue-"


@define(slots=True)
class QueueEntry:
    """One uploaded file's place in the drain order.

    Mutable on purpose: the sequencer advances ``status`` and fills
    ``operation_id``/``run_id`` as the entry starts. All mutations happen in
    synchronous stretches of the single-threaded event loop, so no lock guards
    them.
    """

    filename: str
    position: int
    path: Path
    status: QueueEntryStatus = "queued"
    operation_id: str | None = None
    run_id: str | None = None


@define(slots=True)
class ImportQueue:
    """A user's queued GDPR export: one temp dir, ordered entries."""

    queue_id: str
    user_id: str
    tmpdir: Path
    entries: list[QueueEntry]

    @property
    def is_drained(self) -> bool:
        """True once no entry can still run — every status is terminal."""
        return all(e.status not in ("queued", "running") for e in self.entries)


# One queue per user. A drained queue stays registered until the next POST
# replaces it, so a reloaded tab still sees the finished per-file record.
_queues: dict[str, ImportQueue] = {}


def _queue_slot_token(queue_id: str) -> str:
    return f"queue_{queue_id}"


def _unlink_quietly(path: Path) -> None:
    """Remove an entry's temp file; a metadata op this small never blocks the
    loop meaningfully, and a file that's already gone is not an error."""
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def get_queue(user_id: str) -> ImportQueue | None:
    """The user's current queue — drained or draining — or None."""
    return _queues.get(user_id)


def any_queue_undrained() -> bool:
    """True while any user's queue still has an entry queued or running.

    The in-process half of the pre-deploy busy gate (``/health?busy=true``): a
    queued-not-started entry has NO ``operation_runs`` row, so a runs-only
    check reads "idle" in the gap between files — the gate has to see the
    queue itself or a deploy could land mid-export.
    """
    return any(not queue.is_drained for queue in _queues.values())


def raise_if_queue_active(user_id: str) -> None:
    """409 while a previous queue is still draining.

    Called by the route before any byte lands on disk, and again by
    ``start_queue`` — the streaming writes between the two checks await, so a
    concurrent POST could pass the first check and must still be refused here.
    """
    existing = _queues.get(user_id)
    if existing is not None and not existing.is_drained:
        raise HTTPException(
            status_code=409,
            detail="An import queue is already running. Wait for it to finish "
            "or cancel its remaining files first.",
        )


def start_queue(
    *, user_id: str, tmpdir: Path, entries: list[QueueEntry]
) -> ImportQueue:
    """Register and start draining a freshly uploaded queue.

    Claims one shared concurrency slot for the whole drain (the 429 propagates
    to the route) and replaces only a drained predecessor. The drain task goes
    through ``launch_background`` so ``cancel_all_background_tasks`` settles it
    at shutdown.
    """
    raise_if_queue_active(user_id)
    queue = ImportQueue(
        queue_id=str(uuid4()), user_id=user_id, tmpdir=tmpdir, entries=entries
    )
    acquire_operation_slot(_queue_slot_token(queue.queue_id))
    _queues[user_id] = queue
    launch_background(f"import_queue_{queue.queue_id}", lambda: _run_queue(queue))
    return queue


def cancel_pending(user_id: str) -> ImportQueue | None:
    """Cancel every not-yet-started entry and unlink its file.

    The running entry is untouched — its run finishes (or fails) on its own
    terms and the sequencer then finds nothing left to start.
    """
    queue = _queues.get(user_id)
    if queue is None:
        return None
    for entry in queue.entries:
        if entry.status == "queued":
            entry.status = "cancelled"
            _unlink_quietly(entry.path)
    return queue


async def _run_queue(queue: ImportQueue) -> None:
    """Drain the queue one entry at a time, in upload order.

    A failed entry records its terminal status and the loop CONTINUES — the
    export is independent slices of one history, so halting on a bad file
    would hand back both the babysitting problem and a half-imported history.
    """
    try:
        for entry in queue.entries:
            # DELETE may have cancelled entries while a predecessor ran.
            if entry.status != "queued":
                continue
            entry.status = "running"
            settled = asyncio.Event()

            def _record_terminal(
                status: OperationStatus,
                *,
                _entry: QueueEntry = entry,
                _settled: asyncio.Event = settled,
            ) -> None:
                _entry.status = status
                _settled.set()

            try:
                started = await launch_sse_operation(
                    user_id=queue.user_id,
                    operation_type="import_spotify_history",
                    coro_factory=_spotify_file_import(queue.user_id, entry.path),
                    occupies_slot=False,
                    on_terminal=_record_terminal,
                )
            except Exception:
                # A kickoff failure (audit-row write, registry) has no run to
                # report through — record it here or the entry (and the queue)
                # would read as running forever.
                logger.error(
                    "Failed to launch queued import",
                    queue_id=queue.queue_id,
                    filename=entry.filename,
                    exc_info=True,
                )
                entry.status = "error"
                _unlink_quietly(entry.path)
                continue
            entry.operation_id = started.operation_id
            entry.run_id = started.run_id
            await settled.wait()
    finally:
        release_operation_slot(_queue_slot_token(queue.queue_id))
        # Entry files are unlinked as their runs terminate; this sweeps the
        # directory itself (and, on cancellation mid-drain, whatever remains).
        shutil.rmtree(queue.tmpdir, ignore_errors=True)


def _spotify_file_import(
    user_id: str, path: Path
) -> Callable[[OperationBoundEmitter], Awaitable[object]]:
    """Coroutine factory for one queued file — the same import the single-file
    route ran, with the same unlink-on-terminal ``finally``."""

    async def _import(emitter: OperationBoundEmitter) -> object:
        from src.application.use_cases.import_play_history import run_import

        try:
            return await run_import(
                user_id=user_id,
                service="spotify",
                mode="file",
                file_path=path,
                progress_emitter=emitter,
            )
        finally:
            _unlink_quietly(path)

    return _import


def cleanup_orphaned_queue_dirs() -> None:
    """Remove queue temp dirs no registered queue owns.

    Synchronous filesystem walk — run it via ``asyncio.to_thread`` from the
    lifespan. It runs as an un-awaited startup task while the server is
    already serving, so an early request CAN have registered a queue before
    the sweep fires — any dir a registered queue points at is live and must
    survive; only unregistered prefix-matching dirs are a previous process's
    leftovers.
    """
    # Resolved on both sides: symlinked temp roots (macOS /tmp → /private/tmp)
    # must not make a live dir look unregistered.
    live_dirs = {queue.tmpdir.resolve() for queue in _queues.values()}
    for path in Path(tempfile.gettempdir()).glob(f"{IMPORT_QUEUE_TMPDIR_PREFIX}*"):
        if path.is_dir() and path.resolve() not in live_dirs:
            shutil.rmtree(path, ignore_errors=True)
            logger.info("Removed orphaned import-queue dir", path=str(path))
