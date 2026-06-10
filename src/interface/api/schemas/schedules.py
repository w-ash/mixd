"""Pydantic v2 schemas for the schedule endpoints (v0.8.2).

The wire shape speaks ``schedule_type`` ("daily" | "weekly") — a friendlier
discriminator than "is day_of_week null?". The use case still derives cadence
from ``day_of_week`` alone (the domain's single source of truth), so the request
validator enforces the two stay consistent before the command is built.
"""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from src.application.use_cases._shared.schedule_validators import (
    validate_iana_timezone,
)

ScheduleTypeLiteral = Literal["daily", "weekly"]
ScheduleStatusLiteral = Literal["enabled", "disabled"]
ScheduleTargetLiteral = Literal["workflow", "sync"]

# Validate the IANA zone at the request boundary so a bad zone is a 422 field
# error, not a use-case ValueError surfaced as a generic 400. The use case keeps
# its own check as the non-bypassable backstop (the CLI has no Pydantic layer).
IanaTimezone = Annotated[str, AfterValidator(validate_iana_timezone)]


class ScheduleUpsertRequest(BaseModel):
    """Create-or-replace payload. Bounds mirror the domain validators exactly."""

    schedule_type: ScheduleTypeLiteral
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)
    # 0=Sunday … 6=Saturday — required for weekly, forbidden for daily.
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    timezone: IanaTimezone = "UTC"

    @model_validator(mode="after")
    def _cadence_consistent(self) -> Self:
        if self.schedule_type == "weekly" and self.day_of_week is None:
            raise ValueError("weekly schedule requires day_of_week (0=Sun … 6=Sat)")
        if self.schedule_type == "daily" and self.day_of_week is not None:
            raise ValueError("daily schedule must not set day_of_week")
        return self


class ScheduleToggleRequest(BaseModel):
    """Enable/disable an existing schedule without losing its run history."""

    enabled: bool


class ScheduleResponse(BaseModel):
    """Full schedule projection. ``schedule_type`` / ``target_type`` are read
    straight off the entity's derived ``@property`` values via ``from_attributes``."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    target_type: ScheduleTargetLiteral
    workflow_id: UUID | None
    sync_target: str | None
    schedule_type: ScheduleTypeLiteral
    hour: int
    minute: int
    day_of_week: int | None
    timezone: str
    status: ScheduleStatusLiteral
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_run_status: str | None
    last_error: str | None
    consecutive_failures: int
    run_count: int


class ScheduleListItem(ScheduleResponse):
    """A list-view schedule plus its resolved display label.

    Only the list carries ``target_label`` (the workflow name / friendly sync
    name): single-resource responses feed the picker, which doesn't need it, so
    the name-resolution cost stays on the one read that uses it.
    """

    target_label: str

    @classmethod
    def from_response(cls, base: ScheduleResponse, *, target_label: str) -> Self:
        """Widen a validated ``ScheduleResponse`` with its resolved label.

        Field-by-field off the typed base (not a ``model_dump()`` spread) so the
        projection stays statically checked — a new ScheduleResponse field is a
        compile error here, not a silently-dropped column.
        """
        return cls(
            id=base.id,
            target_type=base.target_type,
            workflow_id=base.workflow_id,
            sync_target=base.sync_target,
            schedule_type=base.schedule_type,
            hour=base.hour,
            minute=base.minute,
            day_of_week=base.day_of_week,
            timezone=base.timezone,
            status=base.status,
            next_run_at=base.next_run_at,
            last_run_at=base.last_run_at,
            last_run_status=base.last_run_status,
            last_error=base.last_error,
            consecutive_failures=base.consecutive_failures,
            run_count=base.run_count,
            target_label=target_label,
        )


class ScheduleListResponse(BaseModel):
    """All of a user's schedules (workflow + sync) for the schedules view."""

    data: list[ScheduleListItem]
