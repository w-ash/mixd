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
from collections.abc import Awaitable, Callable, Generator
import contextlib
import os
from pathlib import Path
import shutil
import tempfile
from typing import Final
from uuid import uuid4

from attrs import define
from fastapi import HTTPException, UploadFile

from src.config import get_logger
from src.config.constants import BusinessLimits
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

# In-flight upload counter — the busy gate's third signal: while a POST is
# still streaming files to disk, no queue is registered and no run row exists,
# so the other two signals both read "idle" and a deploy could land mid-
# upload. Mutations happen in synchronous stretches of the single-threaded
# event loop (increment before the stream's first await, decrement in the
# context manager's ``finally``), so no lock guards the int.
_streaming_uploads = 0


def uploads_streaming() -> int:
    """How many POSTs are currently streaming upload bytes to disk."""
    return _streaming_uploads


@contextlib.contextmanager
def streaming_upload() -> Generator[None]:
    """Mark an upload's streaming phase for the busy gate.

    Held from before ``mkdtemp`` until the queue is registered (or the
    request fails), so ``/health?busy=true`` never reads "idle" while upload
    bytes are landing on disk.
    """
    global _streaming_uploads
    _streaming_uploads += 1
    try:
        yield
    finally:
        _streaming_uploads -= 1


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


async def receive_export_upload(user_id: str, files: list[UploadFile]) -> ImportQueue:
    """Guard, stream, and start one user's GDPR export upload — the whole POST.

    Owns everything between the route's parse and its serialize: the
    active-queue 409, the entry-count 422 and declared-size 413 (declared
    sizes are only a cheap early rejection — the streaming writer re-enforces
    both caps on real bytes), the tmpdir + capped streaming, refused-start
    cleanup, and ``start_queue``. Raises ``HTTPException`` directly, the
    ``sse_operations`` precedent for service-level HTTP errors.
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

    # The busy-gate marker wraps mkdtemp through registration: this is exactly
    # the window where bytes exist on disk that neither the queue registry nor
    # any operation_runs row can vouch for.
    with streaming_upload():
        tmpdir = Path(tempfile.mkdtemp(prefix=IMPORT_QUEUE_TMPDIR_PREFIX))
        entries = await _stream_uploads_to_queue_dir(files, tmpdir)
        try:
            return start_queue(user_id=user_id, tmpdir=tmpdir, entries=entries)
        except BaseException:
            # A refused start (slot 429, or losing the raced 409 re-check)
            # must not strand the streamed bytes — the startup sweep only runs
            # on restart, and autostop is off, so this process may live for
            # weeks.
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise


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
    lifespan. Precondition: no upload may be streaming while it runs — a
    tmpdir exists from ``mkdtemp`` until ``start_queue`` registers it, and a
    concurrent sweep would rmtree those in-flight bytes as "orphans". The
    lifespan enforces this by ordering: the sweep is AWAITED before the app
    starts serving, so no request can be mid-stream yet. The registered-queue
    exclusion below stays as belt-and-braces — any dir a registered queue
    points at is live and must survive; only unregistered prefix-matching
    dirs are a previous process's leftovers.
    """
    # Resolved on both sides: symlinked temp roots (macOS /tmp → /private/tmp)
    # must not make a live dir look unregistered.
    live_dirs = {queue.tmpdir.resolve() for queue in _queues.values()}
    for path in Path(tempfile.gettempdir()).glob(f"{IMPORT_QUEUE_TMPDIR_PREFIX}*"):
        if path.is_dir() and path.resolve() not in live_dirs:
            shutil.rmtree(path, ignore_errors=True)
            logger.info("Removed orphaned import-queue dir", path=str(path))
