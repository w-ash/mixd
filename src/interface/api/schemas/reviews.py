"""Pydantic v2 schemas for match review API endpoints."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from src.application.use_cases.list_match_reviews import ListMatchReviewsResult
from src.application.use_cases.resolve_match_review import ResolveMatchReviewResult
from src.domain.entities.match_review import MatchReview


class MatchReviewSchema(BaseModel):
    """Single match review item for API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int | None
    track_id: int
    connector_name: str
    connector_track_id: int
    match_method: str
    confidence: int
    match_weight: float
    confidence_evidence: dict[str, object] | None
    status: str
    reviewed_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None
    connector_track_title: str
    connector_track_artists: list[str]


class MatchReviewListSchema(BaseModel):
    """Paginated list of match reviews."""

    data: list[MatchReviewSchema]
    total: int
    limit: int
    offset: int


class ResolveReviewRequest(BaseModel):
    """Request body for accepting or rejecting a review."""

    action: Literal["accept", "reject"]


class ResolveReviewResponse(BaseModel):
    """Response after resolving a review."""

    review: MatchReviewSchema
    mapping_created: bool


def to_review_schema(review: MatchReview) -> MatchReviewSchema:
    """Convert domain entity to API schema."""
    return MatchReviewSchema(
        id=review.id,
        track_id=review.track_id,
        connector_name=review.connector_name,
        connector_track_id=review.connector_track_id,
        match_method=review.match_method,
        confidence=review.confidence,
        match_weight=review.match_weight,
        confidence_evidence=review.confidence_evidence,
        status=review.status,
        reviewed_at=review.reviewed_at,
        created_at=review.created_at,
        updated_at=review.updated_at,
        connector_track_title=review.connector_track_title,
        connector_track_artists=review.connector_track_artists,
    )


def to_review_list(result: ListMatchReviewsResult) -> MatchReviewListSchema:
    """Convert use case result to paginated API response."""
    return MatchReviewListSchema(
        data=[to_review_schema(r) for r in result.reviews],
        total=result.total,
        limit=result.limit,
        offset=result.offset,
    )


def to_resolve_response(result: ResolveMatchReviewResult) -> ResolveReviewResponse:
    """Convert resolve use case result to API response."""
    return ResolveReviewResponse(
        review=to_review_schema(result.review),
        mapping_created=result.mapping_created,
    )
