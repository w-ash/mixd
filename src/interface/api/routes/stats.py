"""Dashboard statistics endpoint.

Single GET endpoint that aggregates counts across the user's music library.
Zero business logic — delegates to GetDashboardStatsUseCase.
"""

from fastapi import APIRouter

from src.application.runner import execute_use_case
from src.application.use_cases.get_dashboard_stats import GetDashboardStatsUseCase
from src.interface.api.schemas.stats import DashboardStatsSchema, to_dashboard_stats

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/dashboard")
async def get_dashboard_stats() -> DashboardStatsSchema:
    """Get aggregate statistics for the dashboard."""
    result = await execute_use_case(lambda uow: GetDashboardStatsUseCase().execute(uow))
    return to_dashboard_stats(result)
