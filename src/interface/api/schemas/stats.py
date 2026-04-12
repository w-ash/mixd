"""Pydantic v2 schemas for dashboard statistics API endpoints."""

from pydantic import BaseModel, ConfigDict

from src.application.use_cases.get_dashboard_stats import DashboardStatsResult
from src.application.use_cases.get_match_method_health import MatchMethodHealthResult
from src.domain.entities.integrity import IntegrityReport


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


# ── Integrity report schemas ─────────────────────────────────────


class IntegrityCheckSchema(BaseModel):
    """Single integrity check result."""

    name: str
    status: str
    count: int
    details: list[dict[str, object]] = []


class IntegrityReportSchema(BaseModel):
    """Full integrity report response."""

    checks: list[IntegrityCheckSchema]
    overall_status: str
    total_issues: int


def to_integrity_report(result: IntegrityReport) -> IntegrityReportSchema:
    """Convert use case result to API schema."""
    return IntegrityReportSchema(
        checks=[
            IntegrityCheckSchema(
                name=c.name, status=c.status, count=c.count, details=c.details
            )
            for c in result.checks
        ],
        overall_status=result.overall_status,
        total_issues=result.total_issues,
    )


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


class MatchMethodHealthSchema(BaseModel):
    """Full match method health report response."""

    stats: list[MethodHealthStatSchema]
    total_mappings: int
    recent_days: int


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
            )
            for s in result.stats
        ],
        total_mappings=result.total_mappings,
        recent_days=result.recent_days,
    )
