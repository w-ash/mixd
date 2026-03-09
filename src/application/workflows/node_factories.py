"""Node factories for building music data processing workflows.

This module creates workflow nodes that process track collections through different
stages like filtering, enrichment, transformation, and output. Each node takes
track data, applies specific operations, and passes results to the next stage.

Key components:
- Transform nodes: Filter, sort, deduplicate track collections
- Enricher nodes: Add metadata from external services (Last.fm, Spotify) or internal database
- Destination nodes: Output tracks to files, playlists, or other formats
- Combiner nodes: Merge multiple track collections using set operations

The factories handle configuration parsing and dependency setup so workflow
definitions can focus on data flow rather than implementation details.
"""

# pyright: reportExplicitAny=false, reportAny=false

from collections.abc import Callable
from typing import Any, TypedDict, cast

# Import for enrichment functionality
from src.application.connector_protocols import TrackMetadataConnector
from src.application.use_cases.enrich_tracks import (
    ConnectorType,
    EnrichmentConfig,
    EnrichTracksCommand,
)
from src.config import get_logger
from src.domain.entities.track import Track, TrackList
from src.domain.entities.workflow import TrackDecision
from src.domain.transforms.core import quarantine_invalid_tracks

from .node_context import NodeContext
from .node_registry import NodeFn
from .protocols import MetricConfigProvider, NodeResult
from .transform_definitions import COMBINER_REGISTRY, TRANSFORM_REGISTRY

# Registry type aliases: transform factories take (ctx, config) and return a TrackList→TrackList fn.
type _TransformFn = Callable[[TrackList], TrackList]


type _TransformFactory = Callable[[Any, dict[str, Any]], _TransformFn]

logger = get_logger(__name__)

# === HELPER FUNCTIONS ===


def _quarantine_and_log(tracklist: TrackList, label: str) -> TrackList:
    """Quarantine tracks without database IDs and log if any were removed."""
    valid_tl, quarantined = quarantine_invalid_tracks(tracklist)
    if quarantined:
        logger.warning(
            f"{label}: quarantined {len(quarantined)} tracks without database IDs",
            quarantined_titles=[t.title for t in quarantined[:5]],
        )
    return valid_tl


def _get_connector_metric_names(
    metric_config: MetricConfigProvider,
    connector_name: str,
    requested_attributes: list[str],
) -> list[str]:
    """Get metric names supported by a connector using the metrics registry.

    Uses the proper metrics registry instead of creating extractor functions.
    Maps generic attribute names to service-specific metric names.

    Args:
        metric_config: Provider for metric configuration (from WorkflowContext DI)
        connector_name: Music service identifier ("lastfm", "spotify", etc.)
        requested_attributes: Metadata fields requested ("user_playcount", "explicit_flag", etc.)

    Returns:
        List of metric names that can be resolved for this connector
    """
    # Get all metrics supported by this connector from the registry
    available_metrics = metric_config.get_connector_metrics(connector_name)

    if not available_metrics:
        logger.warning(f"No metrics registered for connector: {connector_name}")
        return []

    # Map requested attributes to actual metric names
    metric_names: list[str] = []
    for attr_name in requested_attributes:
        # Try exact match first
        if attr_name in available_metrics:
            metric_names.append(attr_name)
        # Try with connector prefix
        elif f"{connector_name}_{attr_name}" in available_metrics:
            metric_names.append(f"{connector_name}_{attr_name}")
        else:
            logger.warning(f"Unknown attribute: {attr_name} for {connector_name}")

    logger.debug(
        f"Mapped {len(requested_attributes)} attributes to {len(metric_names)} metrics for {connector_name}"
    )
    return metric_names


# === TRACK DECISION GENERATION ===


def _track_summary(track: Track) -> tuple[str, str]:
    """Extract (title, comma-joined artists) for decision records."""
    title = track.title or "Unknown"
    artists = ", ".join(a.name for a in track.artists) if track.artists else "Unknown"
    return title, artists


def _generate_filter_decisions(
    input_tracks: TrackList,
    output_tracks: TrackList,
    config: dict[str, Any],
) -> list[TrackDecision]:
    """Generate decisions for filter nodes — removed tracks get a reason from config."""
    output_ids = {t.id for t in output_tracks.tracks}
    metric_name = config.get("metric_name")
    min_val = config.get("min_value")
    max_val = config.get("max_value")

    # Build threshold description
    threshold: float | None = None
    if min_val is not None:
        threshold = float(min_val)
        reason = f"Below minimum {metric_name or 'threshold'}: {min_val}"
    elif max_val is not None:
        threshold = float(max_val)
        reason = f"Above maximum {metric_name or 'threshold'}: {max_val}"
    else:
        reason = "Did not pass filter criteria"

    decisions: list[TrackDecision] = []
    for track in input_tracks.tracks:
        title, artists = _track_summary(track)
        if track.id in output_ids:
            decisions.append(
                TrackDecision(
                    track_id=track.id or 0,
                    title=title,
                    artists=artists,
                    decision="kept",
                    reason="Passed filter",
                    metric_name=metric_name,
                )
            )
        else:
            decisions.append(
                TrackDecision(
                    track_id=track.id or 0,
                    title=title,
                    artists=artists,
                    decision="removed",
                    reason=reason,
                    metric_name=metric_name,
                    threshold=threshold,
                )
            )
    return decisions


def _generate_sorter_decisions(
    output_tracks: TrackList,
    config: dict[str, Any],
) -> list[TrackDecision]:
    """Generate decisions for sorter nodes — all kept with rank + sort info."""
    metric_name = config.get("metric_name") or config.get("sort_key")
    decisions: list[TrackDecision] = []
    for rank, track in enumerate(output_tracks.tracks, 1):
        title, artists = _track_summary(track)
        decisions.append(
            TrackDecision(
                track_id=track.id or 0,
                title=title,
                artists=artists,
                decision="kept",
                reason=f"Ranked #{rank}",
                metric_name=metric_name,
                rank=rank,
            )
        )
    return decisions


def _generate_selector_decisions(
    input_tracks: TrackList,
    output_tracks: TrackList,
    config: dict[str, Any],
) -> list[TrackDecision]:
    """Generate decisions for selector nodes — removed tracks trimmed by limit."""
    limit = config.get("count") or config.get("percentage")
    output_ids = {t.id for t in output_tracks.tracks}
    decisions: list[TrackDecision] = []
    for track in input_tracks.tracks:
        title, artists = _track_summary(track)
        if track.id in output_ids:
            decisions.append(
                TrackDecision(
                    track_id=track.id or 0,
                    title=title,
                    artists=artists,
                    decision="kept",
                    reason="Within selection limit",
                )
            )
        else:
            decisions.append(
                TrackDecision(
                    track_id=track.id or 0,
                    title=title,
                    artists=artists,
                    decision="removed",
                    reason=f"Exceeded selection limit ({limit})",
                )
            )
    return decisions


def _generate_combiner_decisions(
    upstream_tracklists: list[TrackList],
    result: TrackList,
    combiner_type: str,
) -> list[TrackDecision]:
    """Generate decisions for combiner nodes — tracks added or removed during merge."""
    result_ids = {t.id for t in result.tracks}
    decisions: list[TrackDecision] = []

    if combiner_type.startswith("intersect"):
        # Intersect: tracks in result are "kept", others are "removed"
        all_input_tracks = {
            t.id: t for tl in upstream_tracklists for t in tl.tracks if t.id is not None
        }
        for track_id, track in all_input_tracks.items():
            title, artists = _track_summary(track)
            if track_id in result_ids:
                decisions.append(
                    TrackDecision(
                        track_id=track_id,
                        title=title,
                        artists=artists,
                        decision="kept",
                        reason="Present in all sources",
                    )
                )
            else:
                decisions.append(
                    TrackDecision(
                        track_id=track_id,
                        title=title,
                        artists=artists,
                        decision="removed",
                        reason="Not present in all sources",
                    )
                )
    else:
        # Concatenate/interleave: all result tracks are "added"
        for track in result.tracks:
            title, artists = _track_summary(track)
            decisions.append(
                TrackDecision(
                    track_id=track.id or 0,
                    title=title,
                    artists=artists,
                    decision="added",
                    reason=f"Combined via {combiner_type}",
                )
            )

    return decisions


# === SHARED NODE IMPLEMENTATION ===


def make_node(
    category: str, node_type: str, operation_name: str | None = None
) -> NodeFn:
    """Build a single-input transform node from registry configuration.

    Args:
        category: Transform category ("filter", "sorter", "selector")
        node_type: Specific operation within category ("deduplicate", "by_metric", etc.)
        operation_name: Optional custom name for logging and debugging

    Returns:
        Async function that processes track collections

    Raises:
        ValueError: If category or node_type is not found in the registry
    """
    if category not in TRANSFORM_REGISTRY:
        raise ValueError(f"Unknown node category: {category}")

    if node_type not in TRANSFORM_REGISTRY[category]:
        raise ValueError(f"Unknown node type: {node_type} in category {category}")

    # Extract factory from TransformEntry
    transform_factory = cast(
        _TransformFactory, TRANSFORM_REGISTRY[category][node_type].factory
    )
    operation = operation_name or f"{category}.{node_type}"

    async def node_impl(context: dict[str, Any], config: dict[str, Any]) -> NodeResult:  # noqa: RUF029
        ctx = NodeContext(context)
        try:
            tracklist = ctx.extract_tracklist()
            tracklist = _quarantine_and_log(tracklist, operation)
            transform = transform_factory(ctx, config)
            result = transform(tracklist)
        except Exception as e:
            logger.error(f"Error in node {operation}: {e}")
            raise
        else:
            input_count = len(tracklist.tracks)
            output_count = len(result.tracks)
            if input_count > 0 and output_count == 0:
                logger.warning(f"{operation}: all {input_count} tracks filtered out")
            else:
                logger.debug(
                    operation,
                    input_count=input_count,
                    output_count=output_count,
                )

            # Generate per-track decisions based on node category
            decisions: list[TrackDecision] = []
            if category == "filter":
                decisions = _generate_filter_decisions(tracklist, result, config)
            elif category == "sorter":
                decisions = _generate_sorter_decisions(result, config)
            elif category == "selector":
                decisions = _generate_selector_decisions(tracklist, result, config)

            return {"tracklist": result, "track_decisions": decisions}

    return node_impl


def make_combiner_node(combiner_type: str) -> NodeFn:
    """Build a multi-input combiner node from the combiner registry.

    Unlike transforms (single TrackList in, single TrackList out), combiners
    collect tracklists from all upstream tasks and merge them.

    Args:
        combiner_type: Combiner operation ("merge_playlists", "interleave_playlists", etc.)

    Returns:
        Async function that combines multiple track collections

    Raises:
        ValueError: If combiner_type is not found in the combiner registry
    """
    if combiner_type not in COMBINER_REGISTRY:
        raise ValueError(f"Unknown combiner type: {combiner_type}")

    combiner_fn = COMBINER_REGISTRY[combiner_type].fn
    operation = f"combiner.{combiner_type}"

    async def node_impl(context: dict[str, Any], config: dict[str, Any]) -> NodeResult:  # noqa: RUF029
        ctx = NodeContext(context)
        upstream_task_ids: list[str] = context.get("upstream_task_ids", [])

        if not upstream_task_ids:
            raise ValueError(f"Combiner node {operation} requires upstream tasks")

        # Single collection point — no double-collection
        upstream_tracklists = ctx.collect_tracklists(upstream_task_ids)

        # Quarantine tracks without database IDs from each upstream
        upstream_tracklists = [
            _quarantine_and_log(tl, operation) for tl in upstream_tracklists
        ]

        # Domain combiners are dual-mode: pass tracklist=TrackList() to get
        # immediate TrackList result rather than a curried Transform function
        deduplicate = config.get("deduplicate", False)
        result = cast(
            TrackList,
            combiner_fn(
                upstream_tracklists, deduplicate=deduplicate, tracklist=TrackList()
            ),
        )

        logger.debug(
            operation,
            input_count=len(upstream_tracklists),
            output_count=len(result.tracks),
        )

        # Generate combiner audit trail
        decisions = _generate_combiner_decisions(
            upstream_tracklists, result, combiner_type
        )
        return {"tracklist": result, "track_decisions": decisions}

    return node_impl


# Config builder type: constructs an EnrichmentConfig from node context and config
type _EnrichmentConfigBuilder = Callable[
    [NodeContext, dict[str, Any]], EnrichmentConfig
]


class _EnricherStaticConfig(TypedDict, total=False):
    """Static registration-time config for enricher nodes (used in node_catalog.py)."""

    connector: ConnectorType
    attributes: list[str]


def build_external_enrichment_config(
    static_config: _EnricherStaticConfig,
) -> _EnrichmentConfigBuilder:
    """Build config builder for external-metadata enrichment (Last.fm, Spotify).

    Captures the static registration-time config (connector name, attribute list)
    and returns a builder that resolves metric names at execution time via DI.

    Raises:
        ValueError: If config doesn't specify a 'connector'
    """
    connector = static_config.get("connector")
    if not connector:
        raise ValueError("Enricher configuration must specify a 'connector' type")
    attribute_names = static_config.get("attributes", ["user_playcount"])

    def builder(ctx: NodeContext, _config: dict[str, Any]) -> EnrichmentConfig:
        workflow_context = ctx.extract_workflow_context()
        metric_names = _get_connector_metric_names(
            workflow_context.metric_config, connector, attribute_names
        )
        return EnrichmentConfig(
            enrichment_type="external_metadata",
            connector=connector,
            connector_instance=cast(
                TrackMetadataConnector, ctx.get_connector(connector)
            ),
            track_metric_names=metric_names,
        )

    return builder


def build_play_history_enrichment_config(
    _ctx: NodeContext, config: dict[str, Any]
) -> EnrichmentConfig:
    """Build config for play-history enrichment from internal database."""
    return EnrichmentConfig(
        enrichment_type="play_history",
        metrics=config.get("metrics", ["total_plays", "last_played_dates"]),
        period_days=config.get("period_days"),
    )


def create_enricher_node(
    build_config: _EnrichmentConfigBuilder,
    enricher_label: str = "play_history",
) -> NodeFn:
    """Create a node that enriches tracks with metadata.

    Args:
        build_config: Callable that constructs an EnrichmentConfig from node context.
        enricher_label: Label for logging (e.g., "lastfm", "play_history").

    Returns:
        Async function that enriches track collections
    """

    async def node_impl(context: dict[str, Any], config: dict[str, Any]) -> NodeResult:
        ctx = NodeContext(context)
        tracklist = ctx.extract_tracklist()
        tracklist = _quarantine_and_log(tracklist, enricher_label)

        logger.info(
            f"Starting {enricher_label} enrichment for {len(tracklist.tracks)} tracks"
        )

        enrichment_config = build_config(ctx, config)
        command = EnrichTracksCommand(
            tracklist=tracklist,
            enrichment_config=enrichment_config,
            progress_manager=context.get("progress_manager"),
            parent_operation_id=context.get("workflow_operation_id"),
        )

        workflow_context = ctx.extract_workflow_context()
        result = await workflow_context.execute_use_case(
            ctx.extract_use_cases().get_enrich_tracks_use_case, command
        )

        metrics_count = sum(len(v) for v in result.metrics_added.values())

        if result.errors and metrics_count == 0:
            logger.warning(
                f"{enricher_label} enrichment failed completely — "
                f"downstream will drop all tracks: {result.errors}"
            )
        elif result.errors:
            logger.warning(
                f"{enricher_label} enrichment had {len(result.errors)} errors "
                f"({metrics_count} metrics still added): {result.errors}"
            )
        else:
            logger.info(
                f"{enricher_label}_enrichment complete",
                metrics_count=metrics_count,
            )
        return {"tracklist": result.enriched_tracklist}

    return node_impl
