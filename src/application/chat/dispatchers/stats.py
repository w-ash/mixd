"""``query_stats`` — the read tool over the user's library-health query paths.

A thin adapter: one ``view`` discriminator selects an existing stats use case
and projects its Result (or domain entity) into a compact, model-facing dict.
No business logic lives here — the aggregation stays in the use cases; this
module only chooses one and flattens it. User-originated free text (connector
track titles/artists on a review row) is wrapped in :class:`UserText` via
``user_text`` so the model boundary quotes it as data.
"""

from collections.abc import Mapping

from src.application.chat.dispatchers._common import (
    opt_choice,
    opt_int,
    user_text,
)
from src.application.chat.protocols import ToolContext
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
from src.application.use_cases.list_match_reviews import (
    ListMatchReviewsCommand,
    ListMatchReviewsUseCase,
)
from src.domain.entities.shared import JsonDict, JsonValue

_VIEWS = ("dashboard", "match_health", "integrity", "match_reviews")


async def _view_dashboard(ctx: ToolContext) -> JsonDict:
    """Library totals and per-connector breakdowns."""
    command = GetDashboardStatsCommand(user_id=ctx.user_id)
    result = await execute_use_case(
        lambda uow: GetDashboardStatsUseCase().execute(command, uow),
        user_id=ctx.user_id,
    )
    return {
        "view": "dashboard",
        "total_tracks": result.total_tracks,
        "total_plays": result.total_plays,
        "total_playlists": result.total_playlists,
        "total_liked": result.total_liked,
        "tracks_by_connector": dict(result.tracks_by_connector),
        "liked_by_connector": dict(result.liked_by_connector),
        "plays_by_connector": dict(result.plays_by_connector),
        "playlists_by_connector": dict(result.playlists_by_connector),
        "preference_counts": {
            str(state): count for state, count in result.preference_counts.items()
        },
    }


async def _view_match_health(ctx: ToolContext) -> JsonDict:
    """Identity-resolution method health plus a few drift headline numbers."""
    command = GetMatchMethodHealthCommand(user_id=ctx.user_id)
    result = await execute_use_case(
        lambda uow: GetMatchMethodHealthUseCase().execute(command, uow),
        user_id=ctx.user_id,
    )
    stats: list[JsonValue] = [
        {
            "match_method": s.match_method,
            "connector_name": s.connector_name,
            "category": s.category,
            "total_count": s.total_count,
            "recent_count": s.recent_count,
            "avg_confidence": s.avg_confidence,
        }
        for s in result.stats
    ]
    return {
        "view": "match_health",
        "total_mappings": result.total_mappings,
        "recent_days": result.recent_days,
        "stats": stats,
        "drift": {
            "review_pending_depth": result.drift.review_pending_depth,
            "review_inflow_30d": result.drift.review_inflow_30d,
        },
    }


async def _view_integrity(ctx: ToolContext) -> JsonDict:
    """Data-consistency check results (the IntegrityReport entity, flattened)."""
    command = CheckDataIntegrityCommand(user_id=ctx.user_id)
    report = await execute_use_case(
        lambda uow: CheckDataIntegrityUseCase().execute(command, uow),
        user_id=ctx.user_id,
    )
    return {
        "view": "integrity",
        "overall_status": report.overall_status,
        "checks": [
            {"name": c.name, "status": c.status, "count": c.count}
            for c in report.checks
        ],
    }


async def _view_match_reviews(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonDict:
    """The pending match-review queue, paginated by limit/offset."""
    limit = opt_int(tool_input, "limit", default=50)
    offset = opt_int(tool_input, "offset", default=0, minimum=0, maximum=100_000)
    command = ListMatchReviewsCommand(user_id=ctx.user_id, limit=limit, offset=offset)
    result = await execute_use_case(
        lambda uow: ListMatchReviewsUseCase().execute(command, uow),
        user_id=ctx.user_id,
    )
    reviews: list[JsonValue] = [
        {
            "review_id": str(r.id),
            "track_id": str(r.track_id),
            "connector_name": r.connector_name,
            "match_method": r.match_method,
            "confidence": r.confidence,
            "status": r.status,
            "connector_track_title": user_text(r.connector_track_title),
            "connector_track_artists": [
                user_text(a) for a in r.connector_track_artists
            ],
        }
        for r in result.reviews
    ]
    return {
        "view": "match_reviews",
        "reviews": reviews,
        "total": result.total,
        "offset": result.offset,
        "limit": result.limit,
    }


async def handle_query_stats(
    tool_input: Mapping[str, JsonValue],
    ctx: ToolContext,
) -> JsonValue:
    """Dispatch one stats ``view`` to its use case and project the result.

    Defaults to the ``dashboard`` view. Unknown views raise
    ``ToolExecutionError`` naming the valid views so the model self-corrects in
    the same turn.
    """
    view = opt_choice(tool_input, "view", _VIEWS, "dashboard")
    if view == "match_health":
        return await _view_match_health(ctx)
    if view == "integrity":
        return await _view_integrity(ctx)
    if view == "match_reviews":
        return await _view_match_reviews(tool_input, ctx)
    return await _view_dashboard(ctx)


QUERY_STATS_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "view": {
            "type": "string",
            "enum": list(_VIEWS),
            "description": (
                "Which stats view to read. 'dashboard' (default): library "
                "totals and per-connector breakdowns. 'match_health': "
                "identity-resolution method health and review drift. "
                "'integrity': data-consistency check results. 'match_reviews': "
                "the pending match-review queue (supports limit/offset)."
            ),
        },
        "limit": {
            "type": "integer",
            "description": "match_reviews only: page size (default 50).",
        },
        "offset": {
            "type": "integer",
            "description": "match_reviews only: rows to skip for paging (default 0).",
        },
    },
    "additionalProperties": False,
}


SPECS: list[dict[str, object]] = [
    {
        "name": "query_stats",
        "description": (
            "Call this to read the user's library health and stats. Pick a "
            "`view`: 'dashboard' for library totals and per-connector "
            "breakdowns, 'match_health' for identity-resolution method health "
            "and review drift, 'integrity' for data-consistency check results, "
            "'match_reviews' for the pending match-review queue (paginate with "
            "limit/offset). Use it before answering questions about counts, "
            "matching quality, data integrity, or the review backlog."
        ),
        "input_schema": QUERY_STATS_INPUT_SCHEMA,
        "dispatch": handle_query_stats,
        "use_cases": (
            "GetDashboardStatsUseCase",
            "GetMatchMethodHealthUseCase",
            "CheckDataIntegrityUseCase",
            "ListMatchReviewsUseCase",
        ),
        "kind": "read",
    },
]
