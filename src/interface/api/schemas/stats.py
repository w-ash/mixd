"""Pydantic v2 schemas for dashboard statistics API endpoints."""

from pydantic import BaseModel, ConfigDict

from src.application.use_cases.get_dashboard_stats import DashboardStatsResult
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
