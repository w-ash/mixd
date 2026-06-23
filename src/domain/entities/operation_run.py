"""OperationRun domain entity.

Persistent audit row for every long-running SSE operation. The seam-level
recorder (``application/services/operation_run_recorder.py``) writes one
row at kickoff with ``status="running"`` and updates it on terminal
events with the final status, merged counts, and any accumulated issues.

Counts and issues are intentionally JSONB-shaped because each operation
type defines its own payload (failed track vs. conflict vs. rate-limit
skip). Normalize when a third consumer needs to query across types.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid7

from attrs import define, field

from .shared import JsonDict, empty_json_map, utc_now_factory

type OperationStatus = Literal["running", "complete", "error", "cancelled"]


def _empty_issues() -> list[JsonDict]:
    """Typed factory for the ``issues`` field default."""
    return []


@define(frozen=True, slots=True)
class OperationRun:
    """One row per SSE-backed operation run."""

    user_id: str
    operation_type: str
    started_at: datetime
    status: OperationStatus
    ended_at: datetime | None = None
    counts: JsonDict = field(factory=empty_json_map)
    issues: list[JsonDict] = field(factory=_empty_issues)
    # Parameters to re-invoke this operation (connector_name + sync_direction for
    # an import), so "Retry failed only" can rebuild the call from the row. Failed
    # item identifiers come from ``issues``; this carries only connector config
    # (strings) — never UUIDs or user_id (the retry route re-derives the owner).
    request_params: JsonDict = field(factory=empty_json_map)
    # The SSE queue key for this run, when launched via the SSE seam. Lets the
    # snapshot / active-operations endpoints resolve it and re-attach the stream.
    operation_id: str | None = None
    # Provenance: the schedule that fired this run, if any (None for runs the
    # user kicked off directly). ON DELETE SET NULL preserves history.
    triggered_by_schedule_id: UUID | None = None
    created_at: datetime = field(factory=utc_now_factory)
    updated_at: datetime = field(factory=utc_now_factory)
    id: UUID = field(factory=uuid7)
