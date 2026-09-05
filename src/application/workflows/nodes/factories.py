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

import asyncio
from collections.abc import Callable, Mapping
from typing import cast

# Import for enrichment functionality
from src.application.connector_protocols import TrackMetadataConnector
from src.application.use_cases.enrich_tracks import (
    EnrichmentConfig,
    EnrichmentType,
    EnrichTracksCommand,
)
from src.application.workflows.protocols import NodeResult
from src.config import get_logger
from src.domain.entities.shared import JsonValue
from src.domain.entities.track import TrackList
from src.domain.transforms.core import require_database_tracks

from .config_accessors import cfg_bool, cfg_int, cfg_str_list
from .execution_context import NodeContext
from .registry import NodeFn
from .transform_definitions import COMBINER_REGISTRY, TRANSFORM_REGISTRY

# Registry type aliases: transform factories take (ctx, config) and return a TrackList→TrackList fn.
type _TransformFn = Callable[[TrackList], TrackList]


type _TransformFactory = Callable[[NodeContext, Mapping[str, JsonValue]], _TransformFn]

logger = get_logger(__name__)

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

    async def node_impl(
        context: dict[str, object], config: Mapping[str, JsonValue]
    ) -> NodeResult:
        ctx = NodeContext(context)
        try:
            tracklist = ctx.extract_tracklist()
            require_database_tracks(tracklist)
            transform = transform_factory(ctx, config)
            # Offload the pure-CPU transform to a worker thread so a large
            # tracklist can't starve the event loop (heartbeat + SSE keep
            # ticking via GIL hand-off). Transforms are pure TrackList→TrackList
            # with no loop-bound state, so this is thread-safe.
            result = await asyncio.to_thread(transform, tracklist)
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

    async def node_impl(
        context: dict[str, object], config: Mapping[str, JsonValue]
    ) -> NodeResult:
        ctx = NodeContext(context)
        upstream_task_ids = ctx.get_upstream_task_ids()

        if not upstream_task_ids:
            raise ValueError(f"Combiner node {operation} requires upstream tasks")

        # Single collection point — no double-collection
        upstream_tracklists = ctx.collect_tracklists(upstream_task_ids)

        # Validate all upstream tracks have database IDs
        for tl in upstream_tracklists:
            require_database_tracks(tl)

        # Domain combiners are dual-mode: pass tracklist=TrackList() to get
        # immediate TrackList result rather than a curried Transform function.
        # Offloaded to a worker thread (pure CPU, no loop-bound state) so merging
        # large tracklists doesn't starve the event loop — see make_node.
        deduplicate = cfg_bool(config, "deduplicate")
        result = cast(
            TrackList,
            await asyncio.to_thread(
                combiner_fn,
                upstream_tracklists,
                deduplicate=deduplicate,
                tracklist=TrackList(),
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


def build_external_enrichment_config(connector: str) -> _EnrichmentConfigBuilder:
    """Build config builder for external-metadata enrichment (Last.fm, Spotify).

    Captures the connector name and returns a builder that resolves the
    connector's metric names at execution time via DI.
    """

    def builder(ctx: NodeContext, _config: Mapping[str, JsonValue]) -> EnrichmentConfig:
        # Metric names resolve here, not at registration: the registry behind
        # them can only see connectors that imported cleanly, so the lookup
        # must not be frozen into module import order.
        workflow_context = ctx.extract_workflow_context()
        return EnrichmentConfig(
            enrichment_type="external_metadata",
            connector=connector,
            connector_instance=ctx.get_connector(
                connector,
                capability="track_enrichment",
                protocol=TrackMetadataConnector,
            ),
            track_metric_names=workflow_context.metric_config.get_connector_metrics(
                connector
            ),
        )

    return builder


def build_play_history_enrichment_config(
    _ctx: NodeContext, config: Mapping[str, JsonValue]
) -> EnrichmentConfig:
    """Build config for play-history enrichment from internal database.

    ``metrics`` carries a declared default (``DEFAULT_PLAY_HISTORY_METRICS``)
    that the executor has already applied.
    """
    return EnrichmentConfig(
        enrichment_type="play_history",
        metrics=cfg_str_list(config, "metrics"),
        period_days=cfg_int(config, "period_days"),
    )


def static_enrichment_config(
    enrichment_type: EnrichmentType,
) -> _EnrichmentConfigBuilder:
    """Build a config builder for an enrichment fully determined by its type.

    Preferences and tags enrichment read only the internal database, so they
    take nothing from the node context or the node's config.

    Args:
        enrichment_type: Enrichment the built config selects.

    Returns:
        Builder returning the same config on every call.
    """

    def builder(
        _ctx: NodeContext, _config: Mapping[str, JsonValue]
    ) -> EnrichmentConfig:
        return EnrichmentConfig(enrichment_type=enrichment_type)

    return builder


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
        context: dict[str, object], config: Mapping[str, JsonValue]
    ) -> NodeResult:
        ctx = NodeContext(context)
        tracklist = ctx.extract_tracklist()
        require_database_tracks(tracklist)

        logger.info(
            f"Starting {enricher_label} enrichment for {len(tracklist.tracks)} tracks"
        )

        enrichment_config = build_config(ctx, config)
        workflow_context = ctx.extract_workflow_context()
        command = EnrichTracksCommand(
            user_id=workflow_context.user_id,
            tracklist=tracklist,
            enrichment_config=enrichment_config,
            progress_broker=ctx.get_progress_broker(),
            parent_operation_id=ctx.get_workflow_operation_id(),
        )

        result = await workflow_context.execute_use_case(
            workflow_context.use_cases.get_enrich_tracks_use_case, command
        )

        # Failure is owned by the use case: EnrichTracksUseCase raises
        # EnrichmentFailedError on a total failure (the executor then degrades)
        # and otherwise returns a zero-error result, so reaching here is success.
        metrics_count = sum(len(v) for v in result.metrics_added.values())
        logger.info(
            f"{enricher_label}_enrichment complete",
            metrics_count=metrics_count,
        )
        return {"tracklist": result.enriched_tracklist}

    return node_impl
