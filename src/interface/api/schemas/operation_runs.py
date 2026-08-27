"""Pydantic v2 schemas for the OperationRun audit-log endpoints (v0.7.7)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, computed_field

from src.domain.entities.shared import JsonDict
from src.interface.api.schemas.cache_tags import CacheTag, touches_for

# Wire mirror of the domain's ``OperationStatus`` — declared explicitly rather
# than re-exported so the generated OpenAPI enum (and the Orval types the web
# reads) stays a stable, inlined literal. ``partial`` = the run finished but
# recorded per-item failures.
OperationStatusLiteral = Literal["running", "complete", "partial", "error", "cancelled"]


class _OperationRunBase(BaseModel):
    """Everything both run projections carry.

    The list and detail rows differ only in how they report issues (a count vs
    the array). Sharing the rest keeps them from drifting — the ``touched`` tags
    especially, since a client recovering from a dropped stream may arrive at
    either endpoint and must get the same answer from both.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    # SSE queue key — lets the operation-awareness UI re-attach the live stream
    # (GET /operations/{operation_id}/progress) for a still-running row.
    operation_id: str | None
    operation_type: str
    started_at: datetime
    ended_at: datetime | None
    status: OperationStatusLiteral
    counts: JsonDict
    # Server truth for the "Retry failed only" action (OperationRun.is_retryable),
    # so the UI never re-derives retryability from operation_type.
    retryable: bool
    # Attribution: "manual" (default), "assistant" (AI-agent-launched),
    # "schedule", or "demand" (a poll pulled in by something reading the data).
    # Drives the badges in the run log.
    initiated_by: str
    # Which surface asked for a demand run — "web", "mcp", or "workflow:<run_id>".
    # Answers "why did this fire?" for unattended runs, where initiated_by alone
    # only says that nobody typed a button.
    trigger_detail: str | None = None

    @computed_field
    @property
    def touched(self) -> list[CacheTag]:
        """Cache tags for the poll-recovery path, when the SSE stream dropped.

        Derived rather than stored, so correcting a tag fixes historic rows too
        and the live and recovered paths cannot disagree.
        """
        return list(touches_for(self.operation_type))


class OperationRunSummarySchema(_OperationRunBase):
    """List row: issue count only, so a 100-issue run doesn't bloat the page."""

    issue_count: int


class OperationRunDetailSchema(_OperationRunBase):
    """Full audit-log row including the issues array."""

    issues: list[JsonDict]


class OperationRunListResponse(BaseModel):
    """List shape: data array plus opaque next-page cursor."""

    data: list[OperationRunSummarySchema]
    limit: int
    next_cursor: str | None
