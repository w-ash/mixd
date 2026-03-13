"""Dashboard statistics and data integrity endpoints.

Aggregates counts across the user's music library and runs data integrity checks.
Zero business logic — delegates to use cases.
"""

from fastapi import APIRouter

from src.application.runner import execute_use_case
from src.application.use_cases.check_data_integrity import (
    CheckDataIntegrityCommand,
    CheckDataIntegrityUseCase,
)
from src.application.use_cases.get_dashboard_stats import (
    GetDashboardStatsCommand,
    GetDashboardStatsUseCase,
)
from src.application.use_cases.get_match_method_health import (
    GetMatchMethodHealthCommand,
    GetMatchMethodHealthUseCase,
)
from src.interface.api.schemas.stats import (
    DashboardStatsSchema,
    IntegrityReportSchema,
    MatchMethodHealthSchema,
    to_dashboard_stats,
    to_integrity_report,
    to_matching_health,
)

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/dashboard")
async def get_dashboard_stats() -> DashboardStatsSchema:
    """Get aggregate statistics for the dashboard."""
    result = await execute_use_case(
        lambda uow: GetDashboardStatsUseCase().execute(GetDashboardStatsCommand(), uow)
    )
    return to_dashboard_stats(result)


@router.get("/integrity")
async def get_integrity_report() -> IntegrityReportSchema:
    """Run data integrity checks and return the report."""
    result = await execute_use_case(
        lambda uow: CheckDataIntegrityUseCase().execute(
            CheckDataIntegrityCommand(), uow
        )
    )
    return to_integrity_report(result)


@router.get("/matching")
async def get_matching_health(recent_days: int = 30) -> MatchMethodHealthSchema:
    """Get match method health statistics."""
    result = await execute_use_case(
        lambda uow: GetMatchMethodHealthUseCase().execute(
            GetMatchMethodHealthCommand(recent_days=recent_days), uow
        )
    )
    return to_matching_health(result)
