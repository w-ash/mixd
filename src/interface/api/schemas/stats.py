"""Pydantic v2 schemas for dashboard statistics API endpoints."""

from pydantic import BaseModel, ConfigDict

from src.application.use_cases.get_dashboard_stats import DashboardStatsResult


class DashboardStatsSchema(BaseModel):
    """Dashboard aggregate statistics response."""

    model_config = ConfigDict(from_attributes=True)

    total_tracks: int
    total_plays: int
    total_playlists: int
    total_liked: int
    tracks_by_connector: dict[str, int]
    liked_by_connector: dict[str, int]


def to_dashboard_stats(result: DashboardStatsResult) -> DashboardStatsSchema:
    """Convert use case result to API schema."""
    return DashboardStatsSchema.model_validate(result)
