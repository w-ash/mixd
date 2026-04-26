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
    created_at: datetime = field(factory=utc_now_factory)
    updated_at: datetime = field(factory=utc_now_factory)
    id: UUID = field(factory=uuid7)
