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

from src.application.transforms import (
    filter_by_metric_range,
    filter_by_play_history,
    sort_by_external_metrics,
    sort_by_play_history,
    weighted_shuffle,
)
from src.domain.transforms import (
    concatenate,
    exclude_artists,
    exclude_tracks,
    filter_by_date_range,
    filter_duplicates,
    interleave,
    select_by_method,
    sort_by_key_function,
)

# === METRIC CLASSIFICATION SYSTEM ===

# Centralized classification of all sortable metrics by data source
TRACK_ATTRIBUTES = {"title", "album", "release_date", "duration_ms", "artist"}

EXTERNAL_METRICS = {
    "spotify_popularity",
    "lastfm_user_playcount",
    "lastfm_listeners",
    "lastfm_global_playcount",
    "danceability",
    "energy",
    "valence",
}

PLAY_HISTORY_METRICS = {
    "total_plays",
    "plays_last_7_days",
    "plays_last_30_days",
    "plays_last_90_days",
    "last_played_date",
}


def _get_metric_category(metric_name: str) -> str:
    """Classify a metric by its data source category.

    Returns:
        "track_attribute", "external_metric", "play_history", or "unknown"
    """
    if metric_name in TRACK_ATTRIBUTES:
        return "track_attribute"
    elif metric_name in EXTERNAL_METRICS:
        return "external_metric"
    elif metric_name in PLAY_HISTORY_METRICS:
        return "play_history"
    else:
        return "unknown"


# === TRACK ATTRIBUTE RESOLUTION ===


def _resolve_sort_key_function(value_name: str):
    """Resolve value name to appropriate key function for track attributes.

    Args:
        value_name: Name of track attribute to sort by

    Returns:
        Key function for extracting the attribute from Track entities
    """
    # Track attributes - extract directly from Track entity
    track_attribute_extractors = {
        "title": lambda track: track.title,
        "album": lambda track: track.album or "",
        "release_date": lambda track: track.release_date,
        "duration_ms": lambda track: track.duration_ms or 0,
        "artist": lambda track: track.artists[0].name if track.artists else "",
    }

    return track_attribute_extractors.get(value_name)


def _route_metric_sorting(cfg: dict):
    """Route metric sorting to appropriate domain function based on data source.

    Clean separation of concerns: application layer makes routing decisions,
    domain layer provides pure functions for each data source type.
    """
    metric_name = cfg.get("metric_name")
    if not metric_name:
        raise ValueError("metric_name is required for metric sorting")

    reverse = cfg.get("reverse", True)
    category = _get_metric_category(metric_name)

    if category == "track_attribute":
        # Route to pure key function sorting
        key_fn = _resolve_sort_key_function(metric_name)
        if key_fn is None:
            raise ValueError(f"Unknown track attribute: {metric_name}")
        return sort_by_key_function(
            key_fn=key_fn,
            reverse=reverse,
            metric_name=metric_name,
        )

    elif category == "external_metric":
        # Route to external metrics sorting (expects metrics in metadata)
        return sort_by_external_metrics(
            metric_name=metric_name,
            reverse=reverse,
        )

    elif category == "play_history":
        # Route to specialized play history sorting
        return sort_by_play_history(
            reverse=reverse,
            # Note: Play history sorting has its own time window parameters
        )

    else:
        # No fallbacks! Force proper classification of all metrics
        raise ValueError(
            f"Unknown metric '{metric_name}' - must be classified in TRACK_ATTRIBUTES, "
            f"EXTERNAL_METRICS, or PLAY_HISTORY_METRICS"
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
        # Unified play history filter with clear time window modes
        "by_play_history": lambda _ctx, cfg: filter_by_play_history(
            min_plays=cfg.get("min_plays"),
            max_plays=cfg.get("max_plays"),
            start_date=cfg.get("start_date"),
            end_date=cfg.get("end_date"),
            min_days_back=cfg.get("min_days_back"),
            max_days_back=cfg.get("max_days_back"),
            include_missing=cfg.get("include_missing", False),
        ),
    },
    "sorter": {
        "by_metric": lambda _ctx, cfg: _route_metric_sorting(cfg),
        "by_release_date": lambda _ctx, cfg: sort_by_key_function(
            # Sort tracks by release date using clean architecture
            key_fn=lambda track: track.release_date,
            metric_name="release_date",
            reverse=cfg.get("reverse", False),  # Default to oldest first
        ),
        # Play history sorter with flexible time window modes
        "by_play_history": lambda _ctx, cfg: sort_by_play_history(
            start_date=cfg.get("start_date"),
            end_date=cfg.get("end_date"),
            min_days_back=cfg.get("min_days_back"),
            max_days_back=cfg.get("max_days_back"),
            reverse=cfg.get("reverse", True),
        ),
        # Weighted shuffle sorter with configurable strength
        "weighted_shuffle": lambda _ctx, cfg: weighted_shuffle(
            cfg.get("shuffle_strength", 0.5),
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
