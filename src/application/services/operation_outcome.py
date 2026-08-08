"""Result → audit-row outcome mapping, shared by every operation surface.

One long-running operation can be started three ways — a user clicking Import
(the SSE seam), the scheduler's heartbeat, or a demand poll pulled in by a read —
and all three finalize the same ``OperationRun``. The mapping from a use case's
returned ``OperationResult`` to that row's terminal status, counts, and issues
lives here so a byte-identical run cannot be recorded differently depending on
what triggered it. It sat in the interface layer until v0.10.2.3, which meant
exactly that: an unattended import that resolved 480 of 500 tracks recorded as a
bare ``error`` with no counts and no issues, while the same run started from the
web recorded as ``partial`` with the failed tracks named.

Application-layer because outcome semantics are the application's to define;
``interface/`` imports this to *present* the result, never to decide it.
"""

from src.domain.entities.operation_run import OperationStatus
from src.domain.entities.operations import OperationResult
from src.domain.entities.shared import JsonDict


def audit_outcome(result: object) -> tuple[OperationStatus, JsonDict | None]:
    """Map a use case's return value to the audit row's terminal status + counts.

    A use case that handled a failure internally returns an ``OperationResult``
    with ``is_failure`` set — the same soft-failure signal the scheduler reads
    (``sync_targets.sync_result_failed``) and the CLI renders. The audit row must
    record that as a failure with the run's counts, not a blanket ``complete``.

    A failure that still landed work (``is_partial_failure``) records as
    ``partial`` instead of ``error``: "the import finished but some tracks
    couldn't be resolved" is a different thing for the user to act on than "the
    import failed". This split is presentation only — ``is_failure`` is untouched,
    so the scheduler's health signal sees a partial run exactly as it did before.

    This is what makes the cycle's headline acceptance — *"if an overnight run
    fails, the user sees it the next time they open mixd"* — true on the web: the
    ``OperationRun`` is the durable, re-attachable record (the live SSE toast is
    gone once the page unmounts). A non-``OperationResult`` return carries no
    failure signal and no counts, so it stays ``complete``.

    Takes ``object`` rather than ``OperationResult`` because the callers hold an
    untyped return: the SSE seam awaits an opaque coroutine and the sync-target
    runner awaits a target's ``run`` hook, neither of which is required to return
    a result at all.
    """
    if isinstance(result, OperationResult):
        status: OperationStatus = "complete"
        if result.is_partial_failure:
            status = "partial"
        elif result.is_failure:
            status = "error"
        return status, result.to_counts()
    return "complete", None


def failure_issues(result: OperationResult) -> list[JsonDict]:
    """Build the run's ``issues`` array from a failed use-case result.

    Ordered: the headline reason (why the run is marked failed), then one issue
    per per-item failure the use case recorded, then a truncation notice when the
    producer's cap dropped some. The per-item dicts pass through as-is — they are
    already the user-legible ``{track, spotify_id, reason}`` shape, and this is
    what puts the exact unresolved tracks in the Import History row.

    A soft failure's detail lives only in ``OperationResult.metadata``, which
    ``to_counts()`` drops — without this it exists nowhere queryable (the
    v0.10.2.2 "errors: 1 with no message" fix, now extended to per-item detail).

    Only meaningful for a result whose ``audit_outcome`` status is in
    ``FAILED_STATUSES``; call sites guard on that rather than this returning an
    empty list for a clean run they should not have asked about.
    """
    issues: list[JsonDict] = []
    failure_message = result.failure_message
    if failure_message:
        issues.append({"message": failure_message[:500]})
    issues.extend(result.resolution_failures)
    truncated = result.resolution_failures_truncated
    if truncated > 0:
        issues.append({"message": f"…and {truncated} more failures — see server logs"})
    return issues


__all__ = ["audit_outcome", "failure_issues"]
