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

# pyright: reportAny=false

from collections.abc import Callable, Mapping
from typing import Any, TypedDict, cast

# Import for enrichment functionality
from src.application.connector_protocols import TrackMetadataConnector
from src.application.use_cases.enrich_tracks import (
    ConnectorType,
    EnrichmentConfig,
    EnrichTracksCommand,
)
from src.config import get_logger
from src.domain.entities.shared import JsonValue
from src.domain.entities.track import TrackList
from src.domain.transforms.core import require_database_tracks

from .config_accessors import cfg_bool, cfg_int, cfg_str_list
from .node_context import NodeContext
from .node_registry import NodeFn
from .protocols import MetricConfigProvider, NodeResult
from .transform_definitions import COMBINER_REGISTRY, TRANSFORM_REGISTRY

# Registry type aliases: transform factories take (ctx, config) and return a TrackList→TrackList fn.
type _TransformFn = Callable[[TrackList], TrackList]


type _TransformFactory = Callable[[NodeContext, Mapping[str, JsonValue]], _TransformFn]

logger = get_logger(__name__)

# === HELPER FUNCTIONS ===


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

    async def node_impl(  # noqa: RUF029
        context: dict[str, Any], config: Mapping[str, JsonValue]
    ) -> NodeResult:
        ctx = NodeContext(context)
        try:
            tracklist = ctx.extract_tracklist()
            require_database_tracks(tracklist)
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

            return {"tracklist": result}

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

    async def node_impl(  # noqa: RUF029
        context: dict[str, Any], config: Mapping[str, JsonValue]
    ) -> NodeResult:
        ctx = NodeContext(context)
        upstream_task_ids: list[str] = context.get("upstream_task_ids", [])

        if not upstream_task_ids:
            raise ValueError(f"Combiner node {operation} requires upstream tasks")

        # Single collection point — no double-collection
        upstream_tracklists = ctx.collect_tracklists(upstream_task_ids)

        # Validate all upstream tracks have database IDs
        for tl in upstream_tracklists:
            require_database_tracks(tl)

        # Domain combiners are dual-mode: pass tracklist=TrackList() to get
        # immediate TrackList result rather than a curried Transform function
        deduplicate = cfg_bool(config, "deduplicate")
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

        return {"tracklist": result}

    return node_impl


# Config builder type: constructs an EnrichmentConfig from node context and config
type _EnrichmentConfigBuilder = Callable[
    [NodeContext, Mapping[str, JsonValue]], EnrichmentConfig
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

    def builder(ctx: NodeContext, _config: Mapping[str, JsonValue]) -> EnrichmentConfig:
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
    _ctx: NodeContext, config: Mapping[str, JsonValue]
) -> EnrichmentConfig:
    """Build config for play-history enrichment from internal database."""
    metrics = cfg_str_list(config, "metrics") or ["total_plays", "last_played_dates"]
    return EnrichmentConfig(
        enrichment_type="play_history",
        metrics=metrics,
        period_days=cfg_int(config, "period_days"),
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

    async def node_impl(
        context: dict[str, Any], config: Mapping[str, JsonValue]
    ) -> NodeResult:
        ctx = NodeContext(context)
        tracklist = ctx.extract_tracklist()
        require_database_tracks(tracklist)

        logger.info(
            f"Starting {enricher_label} enrichment for {len(tracklist.tracks)} tracks"
        )

        enrichment_config = build_config(ctx, config)
        command = EnrichTracksCommand(
            user_id=ctx.extract_workflow_context().user_id,
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
