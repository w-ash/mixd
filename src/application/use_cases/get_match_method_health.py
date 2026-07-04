"""Use case for retrieving match method health statistics.

Aggregates track mapping data by match_method and connector_name to show
which identity resolution strategies are producing mappings and at what
confidence levels. Supports a recency window for trend analysis.

Also assembles "drift signals" — exploratory metrics with no fixed
thresholds, meant to be baselined empirically and compared week-over-week
(see docs/backlog/identity-resolution-design-space.md): recent
search_fallback share per connector, review-queue inflow/depth/age,
isrc_suspect pending depth, confidence/evidence divergence, and stale
denormalized ID drain.
"""

from datetime import UTC, datetime

from attrs import define

from src.config.constants import MatchMethod
from src.domain.repositories.connector import ConnectorRepositoryProtocol
from src.domain.repositories.match_review import MatchReviewRepositoryProtocol
from src.domain.repositories.uow import UnitOfWorkProtocol

# Review-inflow windows for the drift panel (days).
REVIEW_INFLOW_SHORT_WINDOW_DAYS = 7
REVIEW_INFLOW_LONG_WINDOW_DAYS = 30


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
    band_reject: int
    band_review: int
    band_accept: int
    band_certain: int


@define(frozen=True, slots=True)
class FallbackShareStat:
    """Recent search_fallback* share of total recent mappings, per connector.

    ``search_fallback*`` covers both ``search_fallback`` and its stale-id
    variant — a rising share signals dead upstream IDs outpacing direct
    imports for that connector.
    """

    connector_name: str
    recent_total: int
    recent_fallback: int
    fallback_share: float


@define(frozen=True, slots=True)
class MatchingDrift:
    """Exploratory drift signals for the matching-health report.

    No fixed thresholds — baseline empirically and compare week-over-week.
    """

    fallback_shares: list[FallbackShareStat]
    review_inflow_7d: int
    review_inflow_30d: int
    review_pending_depth: int
    review_oldest_pending_days: float | None
    review_pending_by_method: dict[str, int]
    isrc_suspect_pending_count: int
    confidence_evidence_divergence_count: int
    stale_denormalized_ids_count: int


@define(frozen=True, slots=True)
class MatchMethodHealthResult:
    """Aggregate result with all stats and convenience grouping."""

    stats: list[MethodHealthStat]
    total_mappings: int
    recent_days: int
    drift: MatchingDrift

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
            review_repo = uow.get_match_review_repository()

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
                    band_reject=row["band_reject"],
                    band_review=row["band_review"],
                    band_accept=row["band_accept"],
                    band_certain=row["band_certain"],
                )
                for row in rows
            ]

            drift = await _compute_drift(
                connector_repo, review_repo, command.user_id, stats
            )

            return MatchMethodHealthResult(
                stats=stats,
                total_mappings=sum(s.total_count for s in stats),
                recent_days=command.recent_days,
                drift=drift,
            )


async def _compute_drift(
    connector_repo: ConnectorRepositoryProtocol,
    review_repo: MatchReviewRepositoryProtocol,
    user_id: str,
    stats: list[MethodHealthStat],
) -> MatchingDrift:
    """Assemble the drift-signals panel from repo queries + the stats already fetched."""
    pending_by_method = await review_repo.count_pending_by_method(user_id=user_id)
    oldest_reviews, _total = await review_repo.list_pending_reviews(
        user_id=user_id, limit=1, sort_by="created_at_asc"
    )
    oldest_days = None
    if oldest_reviews and oldest_reviews[0].created_at is not None:
        age = datetime.now(UTC) - oldest_reviews[0].created_at
        oldest_days = round(age.total_seconds() / 86400, 1)

    return MatchingDrift(
        fallback_shares=_fallback_shares(stats),
        review_inflow_7d=await review_repo.count_created_since(
            REVIEW_INFLOW_SHORT_WINDOW_DAYS, user_id=user_id
        ),
        review_inflow_30d=await review_repo.count_created_since(
            REVIEW_INFLOW_LONG_WINDOW_DAYS, user_id=user_id
        ),
        review_pending_depth=await review_repo.count_pending(user_id=user_id),
        review_oldest_pending_days=oldest_days,
        review_pending_by_method=pending_by_method,
        isrc_suspect_pending_count=pending_by_method.get(MatchMethod.ISRC_SUSPECT, 0),
        confidence_evidence_divergence_count=(
            await connector_repo.count_confidence_evidence_divergence(user_id=user_id)
        ),
        stale_denormalized_ids_count=(
            await connector_repo.count_stale_denormalized_ids(user_id=user_id)
        ),
    )


def _fallback_shares(stats: list[MethodHealthStat]) -> list[FallbackShareStat]:
    """Recent search_fallback* share of total recent mappings, grouped by connector."""
    totals: dict[str, int] = {}
    fallbacks: dict[str, int] = {}
    for stat in stats:
        totals[stat.connector_name] = (
            totals.get(stat.connector_name, 0) + stat.recent_count
        )
        if stat.match_method.startswith(MatchMethod.SEARCH_FALLBACK):
            fallbacks[stat.connector_name] = (
                fallbacks.get(stat.connector_name, 0) + stat.recent_count
            )
    return [
        FallbackShareStat(
            connector_name=connector_name,
            recent_total=recent_total,
            recent_fallback=fallbacks.get(connector_name, 0),
            fallback_share=(
                round(fallbacks.get(connector_name, 0) / recent_total, 4)
                if recent_total
                else 0.0
            ),
        )
        for connector_name, recent_total in sorted(totals.items())
    ]
