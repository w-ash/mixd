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

from collections.abc import Awaitable, Callable

# Import for enrichment functionality
from src.application.use_cases.enrich_tracks import (
    EnrichmentConfig,
    EnrichTracksCommand,
)

# match_tracks import removed - modern enricher uses TrackMetadataEnricher directly
from src.config import get_logger, settings
from src.domain.entities.track import TrackList

# WorkflowRepositoryAdapter removed - dependencies now injected through protocols
from .destination_nodes import DESTINATION_HANDLERS
from .node_context import NodeContext
from .protocols import WorkflowContext
from .transform_registry import TRANSFORM_REGISTRY

# Type definitions
type NodeFn = Callable[[dict, dict], Awaitable[dict]]

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
    from src.infrastructure.connectors._shared.metrics import get_connector_metrics

    # Get all metrics supported by this connector from the registry
    available_metrics = get_connector_metrics(connector_name)

    if not available_metrics:
        logger.warning(f"No metrics registered for connector: {connector_name}")
        return []

    # Map requested attributes to actual metric names
    metric_names = []
    for attr_name in requested_attributes:
        # Try exact match first
        if attr_name in available_metrics:
            metric_names.append(attr_name)
        # Try with connector prefix
        elif f"{connector_name}_{attr_name}" in available_metrics:
            metric_names.append(f"{connector_name}_{attr_name}")
        # Try common mappings for legacy attribute names
        elif connector_name == "lastfm":
            if (
                attr_name == "user_playcount"
                and "lastfm_user_playcount" in available_metrics
            ):
                metric_names.append("lastfm_user_playcount")
            elif (
                attr_name == "global_playcount"
                and "lastfm_global_playcount" in available_metrics
            ):
                metric_names.append("lastfm_global_playcount")
            elif attr_name == "listeners" and "lastfm_listeners" in available_metrics:
                metric_names.append("lastfm_listeners")
            else:
                logger.warning(f"Unknown attribute: {attr_name} for {connector_name}")
        elif connector_name == "spotify":
            if attr_name == "popularity" and "spotify_popularity" in available_metrics:
                metric_names.append("spotify_popularity")
            elif attr_name == "explicit" and "explicit_flag" in available_metrics:
                metric_names.append("explicit_flag")
            else:
                logger.warning(f"Unknown attribute: {attr_name} for {connector_name}")
        else:
            logger.warning(f"Unknown attribute: {attr_name} for {connector_name}")

    logger.debug(
        f"Mapped {len(requested_attributes)} attributes to {len(metric_names)} metrics for {connector_name}"
    )
    return metric_names


# === CORE NODE FACTORY ===


class WorkflowNodeFactory:
    """Creates workflow processing nodes with shared dependencies and configuration.

    Attributes:
        context: Workflow execution environment with database connections and external services
        logger: Logging instance for tracking node operations
    """

    def __init__(self, context: WorkflowContext):
        """Initialize factory with workflow execution context.

        Args:
            context: Execution environment with database connections and external services
        """
        self.context = context
        self.logger = context.logger

    def make_node(
        self,
        category: str,
        node_type: str,
        operation_name: str | None = None,
    ) -> NodeFn:
        """Create a track processing node from registered transform operations.

        Args:
            category: Transform category ("filter", "sort", "combiner", etc.)
            node_type: Specific operation within category ("by_playcount", "union", etc.)
            operation_name: Optional custom name for logging and debugging

        Returns:
            Async function that processes track collections
        """
        return _create_transform_node_impl(category, node_type, operation_name)

    # === ENRICHER FACTORY ===


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

    async def node_impl(context: dict, config: dict) -> dict:
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

    # Get transform factory from registry
    transform_factory = TRANSFORM_REGISTRY[category][node_type]
    operation = operation_name or f"{category}.{node_type}"

    async def node_impl(context: dict, config: dict) -> dict:  # noqa: RUF029
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

            return {
                "tracklist": result,
                "operation": operation,
                "input_count": len(upstream_tracklists),
                "output_count": len(result.tracks),
            }

        else:
            # Standard case - single upstream dependency
            try:
                # Extract tracklist from primary upstream task
                tracklist = ctx.extract_tracklist()

                # Create and apply the transformation
                transform = transform_factory(ctx, config)
                result = transform(tracklist)

                return {
                    "tracklist": result,
                    "operation": operation,
                    "input_count": len(tracklist.tracks),
                    "output_count": len(result.tracks),
                }
            except Exception as e:
                logger.error(f"Error in node {operation}: {e}")
                raise

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


def create_enricher_node(config: dict) -> NodeFn:
    """Create a node that adds metadata from external music services to tracks.

    Fetches additional track information from services like Last.fm or Spotify
    and attaches it to each track (play counts, artist tags, audio features, etc.).

    Args:
        config: Configuration with required keys:
            - connector: External service name ("lastfm", "spotify")
            - attributes: List of metadata fields to extract
            Optional:
            - max_age_hours: How old cached data can be before refreshing

    Returns:
        Async function that enriches track collections with external metadata

    Raises:
        ValueError: If connector type is not specified in config
    """
    enricher_type = config.get("connector")
    if not enricher_type:
        raise ValueError("Enricher configuration must specify a 'connector' type")

    async def node_impl(context: dict, node_config: dict) -> dict:
        ctx = NodeContext(context)
        tracklist = ctx.extract_tracklist()

        logger.info(
            f"Starting {enricher_type} enrichment for {len(tracklist.tracks)} tracks"
        )

        # Initialize connector instance
        connector_instance = ctx.get_connector(enricher_type)

        # Get use case dependencies
        use_cases = ctx.extract_use_cases()

        # Get freshness configuration for this enricher
        max_age_hours = node_config.get("max_age_hours")
        if max_age_hours is None:
            # Get default freshness requirement from config
            if enricher_type.lower() == "lastfm":
                max_age_hours = settings.freshness.lastfm_hours
            elif enricher_type.lower() == "spotify":
                max_age_hours = settings.freshness.spotify_hours
            elif enricher_type.lower() == "musicbrainz":
                max_age_hours = settings.freshness.musicbrainz_hours
            else:
                max_age_hours = None

        if max_age_hours is not None:
            logger.info(
                f"Using data freshness requirement: {max_age_hours} hours for {enricher_type}"
            )

        # Use EnrichTracksUseCase - already extracted above

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
            max_age_hours=max_age_hours,
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

        # Use standardized result formatter
        return NodeContext.format_enrichment_result(
            operation=f"{enricher_type}_enrichment",
            enriched_tracklist=result.enriched_tracklist,
            metrics=result.metrics_added,
        )

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

    async def node_impl(context: dict, config: dict) -> dict:
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

        # Use standardized result formatter with extra fields
        return NodeContext.format_enrichment_result(
            operation="play_history_enrichment",
            enriched_tracklist=result.enriched_tracklist,
            metrics=result.metrics_added,
            enriched_metrics=list(
                result.metrics_added.keys()
            ),  # Extra field for this operation
        )

    return node_impl
