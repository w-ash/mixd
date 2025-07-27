"""
Transformation strategy implementations for workflow nodes.

This module implements the Strategy pattern for workflow transformations,
providing concrete algorithms that power different node types. Each strategy
is a pure functional transformation that can be composed and configured through
the node factory system.

The module maintains a registry of available strategies organized by category:
- Filters: Strategies that selectively include/exclude tracks
- Sorters: Strategies that reorder tracks based on attributes or metrics
- Selectors: Strategies that select subsets of tracks
- Combiners: Strategies that merge multiple tracklists

Each strategy focuses on a single responsibility and adheres to a consistent
interface, making the system extensible through new strategy implementations.
"""

from src.domain.transforms.core import (
    concatenate,
    exclude_artists,
    exclude_tracks,
    filter_by_date_range,
    filter_by_metric_range,
    filter_by_play_history,
    filter_duplicates,
    interleave,
    select_by_method,
    sort_by_attribute,
)

# === TRANSFORM STRATEGIES ===

TRANSFORM_REGISTRY = {
    "filter": {
        "deduplicate": lambda _ctx, _cfg: filter_duplicates(),
        "by_release_date": lambda _ctx, cfg: filter_by_date_range(
            cfg.get("min_age_days"),
            cfg.get("max_age_days"),
        ),
        "by_tracks": lambda ctx, cfg: exclude_tracks(
            ctx.data[cfg["exclusion_source"]]["tracklist"].tracks,
        ),
        "by_artists": lambda ctx, cfg: exclude_artists(
            ctx.data[cfg["exclusion_source"]]["tracklist"].tracks,
            cfg.get("exclude_all_artists", False),
        ),
        "by_metric": lambda _ctx, cfg: filter_by_metric_range(
            metric_name=cfg["metric_name"],
            min_value=cfg.get("min_value"),
            max_value=cfg.get("max_value"),
            include_missing=cfg.get("include_missing", False),
        ),
        # Unified play history filter with flexible constraints
        "by_play_history": lambda _ctx, cfg: filter_by_play_history(
            min_plays=cfg.get("min_plays"),
            max_plays=cfg.get("max_plays"),
            after_date=cfg.get("after_date"),
            before_date=cfg.get("before_date"),
            days_back=cfg.get("days_back"),
            days_forward=cfg.get("days_forward"),
            include_missing=cfg.get("include_missing", False),
        ),
    },
    "sorter": {
        "by_metric": lambda _ctx, cfg: sort_by_attribute(
            # Dynamic metric sorter - pulls metric_name from config
            key_fn=cfg.get("metric_name"),
            metric_name=cfg.get("metric_name"),
            reverse=cfg.get("reverse", True),
        ),
        "by_release_date": lambda _ctx, cfg: sort_by_attribute(
            # Sort tracks by release date
            key_fn=lambda track: track.release_date,
            metric_name="release_date",
            reverse=cfg.get("reverse", False),  # Default to oldest first
        ),
    },
    "selector": {
        "limit_tracks": lambda _ctx, cfg: select_by_method(
            cfg.get("count", 10),
            cfg.get("method", "first"),
        ),
    },
    "combiner": {
        "merge_playlists": lambda ctx, cfg: concatenate(
            ctx.collect_tracklists(cfg.get("sources", [])),
        ),
        "concatenate_playlists": lambda ctx, cfg: concatenate(
            ctx.collect_tracklists(cfg.get("order", [])),
        ),
        "interleave_playlists": lambda ctx, cfg: interleave(
            ctx.collect_tracklists(cfg.get("sources", [])),
        ),
    },
}
