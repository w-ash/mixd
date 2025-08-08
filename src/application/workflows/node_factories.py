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


def _get_connector_extractors(enricher_type: str, attribute_names: list[str]) -> dict:
    """Build extractor functions for retrieving metadata from music service APIs.

    Maps generic attribute names to service-specific extraction logic since each
    connector (Last.fm, Spotify) has different response formats.

    Args:
        enricher_type: Music service identifier ("lastfm", "spotify", etc.)
        attribute_names: Metadata fields to extract ("user_playcount", "artist_tags", etc.)

    Returns:
        Dictionary mapping attribute names to extractor functions
    """
    try:
        if enricher_type == "lastfm":
            from src.infrastructure.connectors.lastfm import get_connector_config

            connector_config = get_connector_config()
            available_extractors = connector_config.get("extractors", {})

            # Map attribute names to actual extractors
            extractors = {}
            for attr_name in attribute_names:
                # Handle both full names and short names
                if attr_name in available_extractors:
                    extractors[attr_name] = available_extractors[attr_name]
                elif f"lastfm_{attr_name}" in available_extractors:
                    extractors[f"lastfm_{attr_name}"] = available_extractors[
                        f"lastfm_{attr_name}"
                    ]
                else:
                    logger.warning(
                        f"Unknown extractor: {attr_name} for {enricher_type}"
                    )

        elif enricher_type == "spotify":
            from src.infrastructure.connectors.spotify import get_connector_config

            connector_config = get_connector_config()
            available_extractors = connector_config.get("extractors", {})

            # Map attribute names to actual extractors
            extractors = {}
            for attr_name in attribute_names:
                if attr_name in available_extractors:
                    extractors[attr_name] = available_extractors[attr_name]
                elif f"spotify_{attr_name}" in available_extractors:
                    extractors[f"spotify_{attr_name}"] = available_extractors[
                        f"spotify_{attr_name}"
                    ]
                else:
                    logger.warning(
                        f"Unknown extractor: {attr_name} for {enricher_type}"
                    )
        else:
            # Fallback: create simple extractors for unknown connectors
            def make_extractor(field_name):
                return lambda obj: getattr(obj, field_name, None)

            extractors = {attr: make_extractor(attr) for attr in attribute_names}

    except ImportError as e:
        logger.warning(
            f"Could not import connector config for {enricher_type}: {e}, using fallback"
        )

        def make_extractor(field_name):
            return lambda obj: getattr(obj, field_name, None)

        extractors = {attr: make_extractor(attr) for attr in attribute_names}

    return extractors


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

    async def node_impl(context: dict, config: dict) -> dict:  # noqa
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

        # Get extractors from connector configuration
        # The config may specify attribute names, but we need actual extractor functions
        attribute_names = config.get(
            "attributes", ["user_playcount"]
        )  # Default for lastfm

        # Get extractors using helper function
        extractors = _get_connector_extractors(enricher_type, attribute_names)

        # Create enrichment command

        enrichment_config = EnrichmentConfig(
            enrichment_type="external_metadata",
            connector=enricher_type,
            connector_instance=connector_instance,
            extractors=extractors,
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
