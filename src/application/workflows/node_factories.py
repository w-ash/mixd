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

from collections.abc import Callable
from typing import Any, cast

# Import for enrichment functionality
from src.application.use_cases.enrich_tracks import (
    EnrichmentConfig,
    EnrichTracksCommand,
)
from src.config import get_logger
from src.domain.entities.track import TrackList

from .destination_nodes import DESTINATION_HANDLERS
from .node_context import NodeContext
from .node_registry import NodeFn
from .protocols import NodeResult
from .transform_registry import TRANSFORM_REGISTRY

# Registry type aliases: transform factories take (ctx, config) and return a TrackList→TrackList fn.
type _TransformFn = Callable[[TrackList], TrackList]
type _TransformFactory = Callable[[Any, dict[str, Any]], _TransformFn]

logger = get_logger(__name__)

# === HELPER FUNCTIONS ===


def _get_connector_metric_names(
    connector_name: str, requested_attributes: list[str]
) -> list[str]:
    """Get metric names supported by a connector using the metrics registry.

    Uses the proper metrics registry instead of creating extractor functions.
    Maps generic attribute names to service-specific metric names.

    Args:
        connector_name: Music service identifier ("lastfm", "spotify", etc.)
        requested_attributes: Metadata fields requested ("user_playcount", "popularity", etc.)

    Returns:
        List of metric names that can be resolved for this connector
    """
    from src.infrastructure.connectors._shared.metric_registry import (
        MetricConfigProviderImpl,
    )

    metric_config = MetricConfigProviderImpl()

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


# === DESTINATION FACTORY ===


def create_destination_node(destination_type: str) -> NodeFn:
    """Create a node that outputs track collections to files, playlists, or other formats.

    Args:
        destination_type: Output format identifier that maps to a registered handler

    Returns:
        Async function that writes track collections to the specified destination

    Raises:
        ValueError: If the destination type is not supported
    """
    if destination_type not in DESTINATION_HANDLERS:
        raise ValueError(f"Unsupported destination type: {destination_type}")

    handler = DESTINATION_HANDLERS[destination_type]

    async def node_impl(context: dict[str, Any], config: dict[str, Any]) -> NodeResult:
        ctx = NodeContext(context)
        tracklist = ctx.extract_tracklist()

        logger.debug(
            "Destination received tracklist with metrics",
            metrics_keys=list(tracklist.metadata.get("metrics", {}).keys()),
        )

        return await handler(tracklist, config, context)

    return node_impl


# === SHARED NODE IMPLEMENTATION ===


def _create_transform_node_impl(
    category: str, node_type: str, operation_name: str | None = None
) -> NodeFn:
    """Build a track collection transform node from registry configuration.

    Handles both standard transforms (single input) and combiners (multiple inputs).

    Args:
        category: Transform category ("filter", "sort", "combiner", etc.)
        node_type: Specific operation within category ("by_playcount", "union", etc.)
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

    # Get transform factory from registry.
    # cast() informs pyright of the runtime contract: registry values are factories that
    # take (ctx, config) and return a TrackList→TrackList transform.
    transform_factory = cast(_TransformFactory, TRANSFORM_REGISTRY[category][node_type])
    operation = operation_name or f"{category}.{node_type}"

    async def node_impl(context: dict[str, Any], config: dict[str, Any]) -> NodeResult:  # noqa: RUF029
        ctx = NodeContext(context)

        # Special handling for combiners which use multiple upstreams
        if category == "combiner":
            # Get upstream task IDs
            upstream_task_ids = context.get("upstream_task_ids", [])

            if not upstream_task_ids:
                raise ValueError(f"Combiner node {operation} requires upstream tasks")

            # Collect tracklists from all upstream tasks
            upstream_tracklists = ctx.collect_tracklists(upstream_task_ids)

            if not upstream_tracklists:
                raise ValueError(
                    f"No valid tracklists found in upstream tasks for {operation}",
                )

            # Apply transformation using collected tracklists
            transform = transform_factory(ctx, config)
            result = transform(TrackList())  # Transform handles collection

            logger.debug(
                operation,
                input_count=len(upstream_tracklists),
                output_count=len(result.tracks),
            )
            return {"tracklist": result}

        else:
            # Standard case - single upstream dependency
            try:
                # Extract tracklist from primary upstream task
                tracklist = ctx.extract_tracklist()

                # Create and apply the transformation
                transform = transform_factory(ctx, config)
                result = transform(tracklist)
            except Exception as e:
                logger.error(f"Error in node {operation}: {e}")
                raise
            else:
                logger.debug(
                    operation,
                    input_count=len(tracklist.tracks),
                    output_count=len(result.tracks),
                )
                return {"tracklist": result}

    return node_impl


# Compatibility function for existing workflow code
def make_node(
    category: str, node_type: str, operation_name: str | None = None
) -> NodeFn:
    """Create a track collection transform node from registry configuration.

    Convenience function for simple workflows that don't need shared dependencies.

    Args:
        category: Transform category ("filter", "sort", "combiner", etc.)
        node_type: Specific operation within category ("by_playcount", "union", etc.)
        operation_name: Optional custom name for logging and debugging

    Returns:
        Async function that processes track collections
    """
    return _create_transform_node_impl(category, node_type, operation_name)


def create_enricher_node(config: dict[str, Any]) -> NodeFn:
    """Create a node that adds metadata from external music services to tracks.

    Fetches additional track information from services like Last.fm or Spotify
    and attaches it to each track (play counts, artist tags, audio features, etc.).

    Args:
        config: Configuration with required keys:
            - connector: External service name ("lastfm", "spotify")
            - attributes: List of metadata fields to extract

    Returns:
        Async function that enriches track collections with external metadata

    Raises:
        ValueError: If connector type is not specified in config
    """
    enricher_type = config.get("connector")
    if not enricher_type:
        raise ValueError("Enricher configuration must specify a 'connector' type")

    async def node_impl(
        context: dict[str, Any], _node_config: dict[str, Any]
    ) -> NodeResult:
        ctx = NodeContext(context)
        tracklist = ctx.extract_tracklist()

        logger.info(
            f"Starting {enricher_type} enrichment for {len(tracklist.tracks)} tracks"
        )

        # Initialize connector instance
        connector_instance = ctx.get_connector(enricher_type)

        # Get use case dependencies
        use_cases = ctx.extract_use_cases()

        # Get metric names from connector registry
        # The config may specify attribute names, but we need actual metric names
        attribute_names = config.get(
            "attributes", ["user_playcount"]
        )  # Default for lastfm

        # Get metric names using helper function
        metric_names = _get_connector_metric_names(enricher_type, attribute_names)

        # Create enrichment command

        enrichment_config = EnrichmentConfig(
            enrichment_type="external_metadata",
            connector=enricher_type,
            connector_instance=connector_instance,
            track_metric_names=metric_names,
        )

        enrichment_command = EnrichTracksCommand(
            tracklist=tracklist, enrichment_config=enrichment_config
        )

        # Execute enrichment through use case
        workflow_context = ctx.extract_workflow_context()
        result = await workflow_context.execute_use_case(
            use_cases.get_enrich_tracks_use_case, enrichment_command
        )

        if result.errors:
            logger.warning(f"Enrichment had errors: {result.errors}")

        logger.info(
            f"{enricher_type}_enrichment complete",
            metrics_count=sum(len(v) for v in result.metrics_added.values()),
        )
        return {"tracklist": result.enriched_tracklist}

    return node_impl


def create_play_history_enricher_node() -> NodeFn:
    """Create a node that adds listening history metrics from the internal database.

    Enriches tracks with local play history data like total play counts, last played
    dates, or play frequency over time periods. Useful for filtering overplayed
    songs or creating playlists based on personal listening patterns.

    Returns:
        Async function that enriches track collections with play history data.
        Config options: metrics (list), period_days (int)
    """

    async def node_impl(context: dict[str, Any], config: dict[str, Any]) -> NodeResult:
        ctx = NodeContext(context)
        tracklist = ctx.extract_tracklist()

        # Get configuration
        metrics = config.get("metrics", ["total_plays", "last_played_dates"])
        period_days = config.get("period_days")

        logger.info(
            f"Starting play history enrichment for {len(tracklist.tracks)} tracks"
        )

        # Use EnrichTracksUseCase
        use_cases = ctx.extract_use_cases()

        # Create enrichment command

        enrichment_config = EnrichmentConfig(
            enrichment_type="play_history", metrics=metrics, period_days=period_days
        )

        enrichment_command = EnrichTracksCommand(
            tracklist=tracklist, enrichment_config=enrichment_config
        )

        # Execute enrichment through use case
        workflow_context = ctx.extract_workflow_context()
        result = await workflow_context.execute_use_case(
            use_cases.get_enrich_tracks_use_case, enrichment_command
        )

        if result.errors:
            logger.warning(f"Play history enrichment had errors: {result.errors}")

        logger.info(
            "play_history_enrichment complete",
            metrics_count=sum(len(v) for v in result.metrics_added.values()),
            enriched_metrics=list(result.metrics_added.keys()),
        )
        return {"tracklist": result.enriched_tracklist}

    return node_impl
