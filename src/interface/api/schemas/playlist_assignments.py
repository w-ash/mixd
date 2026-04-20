"""Pydantic v2 schemas for playlist assignment endpoints."""

from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from src.domain.entities.playlist_assignment import (
    AssignmentActionType,
    validate_action_value,
)


class CreateAssignmentRequest(BaseModel):
    """Body for POST /api/v1/playlist-assignments.

    ``action_value`` is canonicalized server-side via the same domain
    ``validate_action_value`` that the CLI helper uses — invalid input
    fails fast with 422 before the use case touches the DB.
    """

    connector_playlist_id: UUID
    action_type: AssignmentActionType
    action_value: str

    @model_validator(mode="after")
    def _normalize_action_value(self) -> Self:
        self.action_value = validate_action_value(self.action_type, self.action_value)
        return self


class AssignmentSchema(BaseModel):
    """One PlaylistAssignment row, serialized for the wire."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    connector_playlist_id: UUID
    action_type: AssignmentActionType
    action_value: str


class ApplyResultSchema(BaseModel):
    """Counts from one ApplyPlaylistAssignmentsUseCase run."""

    model_config = ConfigDict(from_attributes=True)

    preferences_applied: int
    preferences_cleared: int
    tags_applied: int
    tags_cleared: int
    conflicts_logged: int
    assignments_processed: int


class CreateAssignmentResponse(BaseModel):
    """Response for POST /api/v1/playlist-assignments.

    Bundles the newly-created assignment AND the engine result from the
    immediate apply, so the Web UI can render the success toast with real
    counts (``"412 tracks tagged mood:chill"``) without a follow-up call.
    """

    assignment: AssignmentSchema
    result: ApplyResultSchema
