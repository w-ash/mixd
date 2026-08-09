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

The drain is itself an operation and the files are its sub-operations, so the
export has ONE ``operation_id`` for its whole life: a client attaches once
rather than re-attaching per file and seeing nothing in between. Reusing the
sub-operation plumbing (``parent_operation_id`` → ``SSEProgressSubscriber``)
means the drain needs no stream vocabulary or endpoint of its own.
"""

import asyncio
from collections.abc import Awaitable, Callable, Generator
import contextlib
from datetime import UTC, datetime
import os
from pathlib import Path
import shutil
import tempfile
from typing import Final
from uuid import uuid4

from attrs import define, field
from fastapi import HTTPException, UploadFile

from src.application.services.progress_broker import get_progress_broker
from src.config import get_logger
from src.config.constants import BusinessLimits, WorkflowConstants
from src.domain.entities.operation_run import OperationStatus
from src.domain.entities.progress import create_progress_event
from src.domain.entities.shared import JsonDict
from src.interface.api.schemas.imports import QueueEntryStatus
from src.interface.api.services.background import (
    finalize_sse_operation,
    launch_background,
)
from src.interface.api.services.progress import (
    OperationBoundEmitter,
    get_operation_registry,
)
from src.interface.api.services.sse_operations import (
    acquire_operation_slot,
    build_terminal_event,
    launch_sse_operation,
    release_operation_slot,
    safe_complete_operation,
    safe_start_operation,
)

logger = get_logger(__name__).bind(service="import_queue")

# Queue temp directories are created with this prefix so a startup sweep can
# tell an orphaned queue dir (previous process) from any other tempdir content.
IMPORT_QUEUE_TMPDIR_PREFIX: Final = "mixd-import-queue-"


@define(slots=True)
class QueueEntry:
    """One uploaded file's place in the drain order.

    Mutable on purpose: the sequencer advances ``status`` and fills the rest as
    the entry starts and settles. All mutations happen in synchronous stretches
    of the single-threaded event loop, so no lock guards them.

    ``counts`` and the timestamps serve the queue endpoint, not the sequencer:
    a file's own stream closes moments after it settles, so this is the only
    place its numbers survive for a client that reloads later.
    """

    filename: str
    position: int
    path: Path
    size_bytes: int = 0
    status: QueueEntryStatus = "queued"
    operation_id: str | None = None
    run_id: str | None = None
    started_at: datetime | None = None
    settled_at: datetime | None = None
    counts: JsonDict | None = None

    @property
    def is_settled(self) -> bool:
        """True once this entry can no longer change — its status is terminal."""
        return self.status not in ("queued", "running")


@define(slots=True)
class ImportQueue:
    """A user's queued GDPR export: one temp dir, ordered entries.

    ``operation_id`` is the drain's own SSE handle — the parent operation whose
    sub-operations are the files. Minted before the first file starts and valid
    until the whole export settles.
    """

    queue_id: str
    user_id: str
    tmpdir: Path
    entries: list[QueueEntry]
    operation_id: str
    started_at: datetime = field(factory=lambda: datetime.now(UTC))

    @property
    def is_drained(self) -> bool:
        """True once no entry can still run — every status is terminal."""
        return all(e.is_settled for e in self.entries)

    @property
    def settled_count(self) -> int:
        return sum(1 for e in self.entries if e.is_settled)


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


async def start_queue(
    *, user_id: str, tmpdir: Path, entries: list[QueueEntry]
) -> ImportQueue:
    """Register and start draining a freshly uploaded queue.

    Claims one shared concurrency slot for the whole drain (the 429 propagates
    to the route) and replaces only a drained predecessor. The drain task goes
    through ``launch_background`` so ``cancel_all_background_tasks`` settles it
    at shutdown.

    The 409 re-check, the slot claim and the registry insert must stay one
    synchronous stretch: the caller has already awaited its way through
    streaming megabytes, so a concurrent POST is real, and an await between
    check and set would let both through — the loser's drain then holds a slot
    and a temp dir nothing can see or sweep. Registration waits until the claim
    is won so a refused claim leaves no orphan stream.

    The stream is registered here, not in ``_run_queue``, so the id the POST
    returns is already streamable rather than racing the background task.

    No ``operation_runs`` row is written for the drain: the durable record is
    one row per file, the granularity a user retries and inspects at.
    """
    raise_if_queue_active(user_id)
    queue = ImportQueue(
        queue_id=str(uuid4()),
        user_id=user_id,
        tmpdir=tmpdir,
        entries=entries,
        operation_id=str(uuid4()),
    )
    acquire_operation_slot(_queue_slot_token(queue.queue_id))
    _queues[user_id] = queue

    _ = await get_operation_registry().register(queue.operation_id)
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
            return await start_queue(user_id=user_id, tmpdir=tmpdir, entries=entries)
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
            file_bytes = await _stream_upload_capped(file, path, total_bytes)
            total_bytes += file_bytes
            entries.append(
                QueueEntry(
                    filename=file.filename or f"file-{position + 1}.json",
                    position=position,
                    path=path,
                    size_bytes=file_bytes,
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


async def _stream_upload_capped(
    file: UploadFile, path: Path, preceding_bytes: int
) -> int:
    """Stream one upload to ``path``; return the bytes THIS file contributed.

    ``preceding_bytes`` is what the queue already holds, so the whole-queue cap
    is still checked against the running total. The per-file return is what
    lets the caller size each entry — a "time left" estimate weighted by work
    needs it, since a GDPR export's files differ several-fold.

    Caps are checked before each write, so no byte past either limit lands.
    """
    # os.* rather than pathlib for async-safe file I/O (ASYNC240).
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        file_bytes = 0
        while chunk := await file.read(64 * 1024):
            file_bytes += len(chunk)
            _raise_if_over_upload_caps(file_bytes, preceding_bytes + file_bytes)
            os.write(fd, chunk)
    finally:
        os.close(fd)
    return file_bytes


def cancel_pending(user_id: str) -> ImportQueue | None:
    """Cancel every not-yet-started entry and unlink its file.

    The running entry is untouched — its run finishes (or fails) on its own
    terms and the sequencer then finds nothing left to start.
    """
    queue = _queues.get(user_id)
    if queue is None:
        return None
    now = datetime.now(UTC)
    for entry in queue.entries:
        if entry.status == "queued":
            entry.status = "cancelled"
            entry.settled_at = now
            _unlink_quietly(entry.path)
    return queue


async def _run_queue(queue: ImportQueue) -> None:
    """Drain the queue one entry at a time, in upload order.

    A failed entry records its terminal status and the loop CONTINUES — the
    export is independent slices of one history, so halting on a bad file
    would hand back both the babysitting problem and a half-imported history.

    The drain runs as an operation in its own right, so one stream covers the
    whole export: each file announces itself on it as a sub-operation, and the
    loop reports overall position between files.
    """
    await safe_start_operation(queue.operation_id, "Import Spotify Export")
    cancellation: asyncio.CancelledError | None = None
    try:
        await _drain_entries(queue)
    except asyncio.CancelledError as exc:
        # Re-raised after cleanup so the task still ends cancelled, and recorded
        # so the drain reports `error` rather than a false success (v0.10.2.5).
        cancellation = exc
    finally:
        status: OperationStatus = (
            "error"
            if cancellation is not None or _every_entry_failed(queue)
            else "complete"
        )
        # Before any await: a second cancellation is delivered at the next
        # suspension point, and nothing reaps a leaked slot.
        release_operation_slot(_queue_slot_token(queue.queue_id))
        # Entry files are unlinked as their runs terminate; this sweeps the
        # directory itself (and, on cancellation mid-drain, whatever remains).
        shutil.rmtree(queue.tmpdir, ignore_errors=True)
        await _emit_queue_position(queue, None)
        await _push_drain_terminal(queue, status)
        await safe_complete_operation(queue.operation_id, status)
        # Last: this holds the task open for the SSE read window, and past it a
        # re-attach 404s onto the queue endpoint's equivalent record. A cancelled
        # drain skips the window — no client is reading, and 30s here would eat
        # the shared shutdown budget.
        await finalize_sse_operation(
            queue.operation_id,
            grace_period_seconds=0.0 if cancellation is not None else None,
        )
    if cancellation is not None:
        raise cancellation


async def _drain_entries(queue: ImportQueue) -> None:
    """Run each still-queued entry to its terminal, in order."""
    for entry in queue.entries:
        # DELETE may have cancelled entries while a predecessor ran.
        if entry.status != "queued":
            continue
        entry.status = "running"
        entry.started_at = datetime.now(UTC)
        await _emit_queue_position(queue, entry)
        settled = asyncio.Event()

        def _record_terminal(
            status: OperationStatus,
            counts: JsonDict | None,
            *,
            _entry: QueueEntry = entry,
            _settled: asyncio.Event = settled,
        ) -> None:
            _entry.status = status
            _entry.counts = counts
            _entry.settled_at = datetime.now(UTC)
            _settled.set()

        try:
            started = await launch_sse_operation(
                user_id=queue.user_id,
                operation_type="import_spotify_history",
                coro_factory=_spotify_file_import(queue.user_id, entry.path),
                occupies_slot=False,
                parent_operation_id=queue.operation_id,
                on_terminal=_record_terminal,
            )
        except Exception:
            # A kickoff failure has no run to report through, so the entry would
            # read as running forever without this.
            logger.error(
                "Failed to launch queued import",
                queue_id=queue.queue_id,
                filename=entry.filename,
                exc_info=True,
            )
            entry.status = "error"
            entry.settled_at = datetime.now(UTC)
            _unlink_quietly(entry.path)
            continue
        entry.operation_id = started.operation_id
        entry.run_id = started.run_id
        await settled.wait()


async def _push_drain_terminal(queue: ImportQueue, status: OperationStatus) -> None:
    """Close the export with a verdict and a per-status tally.

    Written here, not by the subscriber, which sees the lifecycle but not the
    outcome. The tally stays per-status because "12 of 13 imported, 1 failed"
    is the part a user acts on.
    """
    registry = get_operation_registry()
    sse_queue = await registry.get_queue(queue.operation_id)
    if sse_queue is None:
        return
    tally: dict[str, int] = {"files": len(queue.entries)}
    for entry in queue.entries:
        key = f"files_{entry.status}"
        tally[key] = tally.get(key, 0) + 1
    await sse_queue.put(
        build_terminal_event(
            "evt_final",
            WorkflowConstants.SSE_EVENT_ERROR
            if status == "error"
            else WorkflowConstants.SSE_EVENT_COMPLETE,
            queue.operation_id,
            "failed" if status == "error" else "completed",
            # Same ``counts`` slot a single run's terminal uses, so a client
            # reads an export's outcome through the code path it already has.
            counts=tally,
        )
    )


def _every_entry_failed(queue: ImportQueue) -> bool:
    """True when nothing in the export got through.

    The only case the drain reports as ``error``: one bad file out of thirteen
    is a result to read, not a failed export.
    """
    return all(entry.status == "error" for entry in queue.entries)


async def _emit_queue_position(queue: ImportQueue, entry: QueueEntry | None) -> None:
    """Report how far through the export the drain is.

    Counted in settled *files*, which makes it monotonic by construction: a
    figure blended with the running file's own percentage rewinds every time
    the queue advances. Fine-grained movement belongs to the running file.
    """
    settled = queue.settled_count
    total = len(queue.entries)
    message = (
        f"File {min(settled + 1, total)} of {total} — {entry.filename}"
        if entry is not None
        else f"Imported {settled} of {total} files"
    )
    try:
        await get_progress_broker().emit_progress(
            create_progress_event(
                operation_id=queue.operation_id,
                current=settled,
                total=total,
                message=message,
            )
        )
    except Exception:
        # Progress tracking must never break the drain it observes — the same
        # rule ``safe_start_operation`` follows on either side of this loop.
        logger.warning(
            "Failed to emit queue progress (continuing)",
            queue_id=queue.queue_id,
            exc_info=True,
        )


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
