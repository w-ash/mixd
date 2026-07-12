"""Pydantic v2 schemas for the OperationRun audit-log endpoints (v0.7.7)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from src.domain.entities.shared import JsonDict

OperationStatusLiteral = Literal["running", "complete", "error", "cancelled"]


class OperationRunSummarySchema(BaseModel):
    """Lightweight row for the list view (no full ``issues`` payload).

    The list endpoint returns this shape; full ``issues`` come back from
    the per-run detail endpoint so a 100-issue run doesn't bloat the
    list response.
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
    issue_count: int
    # Server truth for the "Retry failed only" action (OperationRun.is_retryable),
    # so the UI never re-derives retryability from operation_type.
    retryable: bool
    # Attribution: "manual" (default), "assistant" (AI-agent-launched), or
    # "schedule". Drives the "Assistant" badge in the run log.
    initiated_by: str


class OperationRunDetailSchema(BaseModel):
    """Full audit-log row including the issues array."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    operation_id: str | None
    operation_type: str
    started_at: datetime
    ended_at: datetime | None
    status: OperationStatusLiteral
    counts: JsonDict
    issues: list[JsonDict]
    retryable: bool
    # Attribution: "manual" (default), "assistant" (AI-agent-launched), or
    # "schedule". Drives the "Assistant" badge in the run log.
    initiated_by: str


class OperationRunListResponse(BaseModel):
    """List shape: data array plus opaque next-page cursor."""

    data: list[OperationRunSummarySchema]
    limit: int
    next_cursor: str | None
