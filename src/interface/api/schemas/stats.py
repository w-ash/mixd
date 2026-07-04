"""Pydantic v2 schemas for dashboard statistics API endpoints."""

from pydantic import BaseModel, ConfigDict

from src.application.use_cases.get_dashboard_stats import DashboardStatsResult
from src.application.use_cases.get_match_method_health import (
    MatchingDrift,
    MatchMethodHealthResult,
)


class DashboardStatsSchema(BaseModel):
    """Dashboard aggregate statistics response."""

    model_config = ConfigDict(from_attributes=True)

    total_tracks: int
    total_plays: int
    total_playlists: int
    total_liked: int
    tracks_by_connector: dict[str, int]
    liked_by_connector: dict[str, int]
    plays_by_connector: dict[str, int]
    playlists_by_connector: dict[str, int]
    preference_counts: dict[str, int]


def to_dashboard_stats(result: DashboardStatsResult) -> DashboardStatsSchema:
    """Convert use case result to API schema."""
    return DashboardStatsSchema.model_validate(result)


# ── Match method health schemas ──────────────────────────────────


class MethodHealthStatSchema(BaseModel):
    """Single match method + connector aggregation row."""

    model_config = ConfigDict(from_attributes=True)

    match_method: str
    connector_name: str
    category: str
    description: str
    total_count: int
    recent_count: int
    avg_confidence: float
    min_confidence: int
    max_confidence: int
    band_reject: int
    band_review: int
    band_accept: int
    band_certain: int


class FallbackShareSchema(BaseModel):
    """Recent search_fallback* share of total recent mappings for one connector."""

    model_config = ConfigDict(from_attributes=True)

    connector_name: str
    recent_total: int
    recent_fallback: int
    fallback_share: float


class MatchingDriftSchema(BaseModel):
    """Drift signals for the matching-health dashboard.

    Exploratory metrics with no fixed thresholds — baseline empirically and
    compare week-over-week.
    """

    fallback_shares: list[FallbackShareSchema]
    review_inflow_7d: int
    review_inflow_30d: int
    review_pending_depth: int
    review_oldest_pending_days: float | None
    review_pending_by_method: dict[str, int]
    isrc_suspect_pending_count: int
    confidence_evidence_divergence_count: int
    stale_denormalized_ids_count: int


class MatchMethodHealthSchema(BaseModel):
    """Full match method health report response."""

    stats: list[MethodHealthStatSchema]
    total_mappings: int
    recent_days: int
    drift: MatchingDriftSchema


def _to_drift_schema(drift: MatchingDrift) -> MatchingDriftSchema:
    """Convert use case drift result to API schema."""
    return MatchingDriftSchema(
        fallback_shares=[
            FallbackShareSchema(
                connector_name=f.connector_name,
                recent_total=f.recent_total,
                recent_fallback=f.recent_fallback,
                fallback_share=f.fallback_share,
            )
            for f in drift.fallback_shares
        ],
        review_inflow_7d=drift.review_inflow_7d,
        review_inflow_30d=drift.review_inflow_30d,
        review_pending_depth=drift.review_pending_depth,
        review_oldest_pending_days=drift.review_oldest_pending_days,
        review_pending_by_method=drift.review_pending_by_method,
        isrc_suspect_pending_count=drift.isrc_suspect_pending_count,
        confidence_evidence_divergence_count=drift.confidence_evidence_divergence_count,
        stale_denormalized_ids_count=drift.stale_denormalized_ids_count,
    )


def to_matching_health(result: MatchMethodHealthResult) -> MatchMethodHealthSchema:
    """Convert use case result to API schema."""
    return MatchMethodHealthSchema(
        stats=[
            MethodHealthStatSchema(
                match_method=s.match_method,
                connector_name=s.connector_name,
                category=s.category,
                description=s.description,
                total_count=s.total_count,
                recent_count=s.recent_count,
                avg_confidence=s.avg_confidence,
                min_confidence=s.min_confidence,
                max_confidence=s.max_confidence,
                band_reject=s.band_reject,
                band_review=s.band_review,
                band_accept=s.band_accept,
                band_certain=s.band_certain,
            )
            for s in result.stats
        ],
        total_mappings=result.total_mappings,
        recent_days=result.recent_days,
        drift=_to_drift_schema(result.drift),
    )
