"""Use case for retrieving match method health statistics.

Aggregates track mapping data by match_method and connector_name to show
which identity resolution strategies are producing mappings and at what
confidence levels. Supports a recency window for trend analysis.
"""

from attrs import define

from src.config.constants import MatchMethod
from src.domain.repositories.interfaces import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class GetMatchMethodHealthCommand:
    """Parameters for the match method health report."""

    user_id: str
    recent_days: int = 30


@define(frozen=True, slots=True)
class MethodHealthStat:
    """Enriched row with category and description from MatchMethod constants."""

    match_method: str
    connector_name: str
    category: str
    description: str
    total_count: int
    recent_count: int
    avg_confidence: float
    min_confidence: int
    max_confidence: int


@define(frozen=True, slots=True)
class MatchMethodHealthResult:
    """Aggregate result with all stats and convenience grouping."""

    stats: list[MethodHealthStat]
    total_mappings: int
    recent_days: int

    @property
    def by_category(self) -> dict[str, list[MethodHealthStat]]:
        """Group stats by category for display."""
        groups: dict[str, list[MethodHealthStat]] = {}
        for stat in self.stats:
            groups.setdefault(stat.category, []).append(stat)
        return groups


@define(slots=True)
class GetMatchMethodHealthUseCase:
    """Use case for retrieving match method health statistics."""

    async def execute(
        self, command: GetMatchMethodHealthCommand, uow: UnitOfWorkProtocol
    ) -> MatchMethodHealthResult:
        """Execute the match method health aggregation."""
        async with uow:
            connector_repo = uow.get_connector_repository()
            rows = await connector_repo.get_match_method_stats(
                user_id=command.user_id,
                recent_days=command.recent_days,
            )
            stats = [
                MethodHealthStat(
                    match_method=row["match_method"],
                    connector_name=row["connector_name"],
                    category=MatchMethod.CATEGORIES.get(row["match_method"], "Unknown"),
                    description=MatchMethod.DESCRIPTIONS.get(row["match_method"], ""),
                    total_count=row["total_count"],
                    recent_count=row["recent_count"],
                    avg_confidence=row["avg_confidence"],
                    min_confidence=row["min_confidence"],
                    max_confidence=row["max_confidence"],
                )
                for row in rows
            ]
            return MatchMethodHealthResult(
                stats=stats,
                total_mappings=sum(s.total_count for s in stats),
                recent_days=command.recent_days,
            )
