"""Workflow node definitions mapping config keys to transform functions.

This is a lookup table, not logic. Each entry maps a workflow config key
(e.g., "filter.by_metric") to the domain or metadata_transforms function
that implements it, plus a human-readable description for the CLI.

To add a new transform:
1. Write the function in domain/transforms/ (pure) or metadata_transforms/ (impure)
2. Add a TransformEntry here pointing to it
3. It auto-registers as a workflow node via node_catalog.py
"""

from collections.abc import Callable, Mapping
from operator import attrgetter
from typing import NamedTuple, Protocol

from src.application.metadata_transforms import (
    filter_by_explicit,
    filter_by_metric_range,
    filter_by_play_history,
    sort_by_date,
    sort_by_play_history,
    weighted_shuffle,
)
from src.application.metadata_transforms.metric_routing import route_metric_sorting
from src.domain.entities.shared import JsonValue
from src.domain.entities.track import TrackList
from src.domain.transforms import (
    concatenate,
    exclude_artists,
    exclude_tracks,
    filter_by_date_range,
    filter_by_duration,
    filter_by_liked_status,
    filter_duplicates,
    interleave,
    intersect,
    reverse_tracks,
    select_by_method,
    select_by_percentage,
    sort_by_key_function,
)
from src.domain.transforms.core import Transform

from .config_accessors import cfg_bool, cfg_float, cfg_int, cfg_str, cfg_str_or_none
from .node_context import NodeContext

# Transform factory: takes (context, config) and returns a TrackList→TrackList transform.
type TransformFactory = Callable[
    [NodeContext, Mapping[str, JsonValue]], Transform | TrackList
]


class CombinerFn(Protocol):
    """Combiner: list[TrackList] → TrackList (dual-mode, returns Transform when curried)."""

    def __call__(
        self,
        tracklists: list[TrackList],
        *,
        deduplicate: bool = ...,
        tracklist: TrackList | None = ...,
    ) -> Transform | TrackList: ...


class TransformEntry(NamedTuple):
    """Transform factory with metadata for auto-registration."""

    factory: TransformFactory
    description: str


class CombinerEntry(NamedTuple):
    """Combiner function with metadata for auto-registration."""

    fn: CombinerFn
    description: str


def _tf(factory: TransformFactory, description: str) -> TransformEntry:
    """Typed TransformEntry constructor enabling lambda type inference.

    basedpyright can't infer lambda parameter types from NamedTuple field context,
    but CAN infer them from an explicit Callable parameter type via bidirectional
    type inference. This gives every lambda in the registry typed `ctx` and `cfg`.
    """
    return TransformEntry(factory, description)


# === TRANSFORM STRATEGIES ===

TRANSFORM_REGISTRY: dict[str, dict[str, TransformEntry]] = {
    "filter": {
        "deduplicate": _tf(
            lambda _ctx, _cfg: filter_duplicates(),
            "Removes duplicate tracks",
        ),
        "by_release_date": _tf(
            lambda _ctx, cfg: filter_by_date_range(
                cfg_int(cfg, "min_age_days"),
                cfg_int(cfg, "max_age_days"),
            ),
            "Filters tracks by release date range",
        ),
        "by_tracks": _tf(
            lambda ctx, cfg: exclude_tracks(
                ctx.collect_tracklists([cfg_str(cfg, "exclusion_source")])[0].tracks,
            ),
            "Excludes tracks from input that are present in exclusion source",
        ),
        "by_artists": _tf(
            lambda ctx, cfg: exclude_artists(
                ctx.collect_tracklists([cfg_str(cfg, "exclusion_source")])[0].tracks,
                cfg_bool(cfg, "exclude_all_artists"),
            ),
            "Excludes tracks whose artists appear in exclusion source",
        ),
        "by_metric": _tf(
            lambda _ctx, cfg: filter_by_metric_range(
                metric_name=cfg_str(cfg, "metric_name"),
                min_value=cfg_float(cfg, "min_value"),
                max_value=cfg_float(cfg, "max_value"),
                include_missing=cfg_bool(cfg, "include_missing"),
            ),
            "Filters tracks based on metric value range",
        ),
        "by_duration": _tf(
            lambda _ctx, cfg: filter_by_duration(
                min_ms=cfg_int(cfg, "min_ms"),
                max_ms=cfg_int(cfg, "max_ms"),
                include_missing=cfg_bool(cfg, "include_missing"),
            ),
            "Filters tracks by duration range (milliseconds)",
        ),
        "by_liked_status": _tf(
            lambda _ctx, cfg: filter_by_liked_status(
                service=cfg_str(cfg, "service"),
                is_liked=cfg_bool(cfg, "is_liked", True),
            ),
            "Filters tracks by liked status on a specific service",
        ),
        "by_explicit": _tf(
            lambda _ctx, cfg: filter_by_explicit(
                keep=cfg_str(cfg, "keep", "all"),
            ),
            "Filters tracks by explicit content flag",
        ),
        "by_play_history": _tf(
            lambda _ctx, cfg: filter_by_play_history(
                min_plays=cfg_int(cfg, "min_plays"),
                max_plays=cfg_int(cfg, "max_plays"),
                start_date=cfg_str_or_none(cfg, "start_date"),
                end_date=cfg_str_or_none(cfg, "end_date"),
                min_days_back=cfg_int(cfg, "min_days_back"),
                max_days_back=cfg_int(cfg, "max_days_back"),
                include_missing=cfg_bool(cfg, "include_missing"),
            ),
            "Filters tracks by play count and/or listening date with flexible constraints",
        ),
    },
    "sorter": {
        "by_metric": _tf(
            lambda _ctx, cfg: route_metric_sorting(cfg),
            "Sorts tracks by any metric specified in config",
        ),
        "by_release_date": _tf(
            lambda _ctx, cfg: sort_by_key_function(
                key_fn=attrgetter("release_date"),
                metric_name="release_date",
                reverse=cfg_bool(cfg, "reverse"),
            ),
            "Sorts tracks by release date",
        ),
        "by_play_history": _tf(
            lambda _ctx, cfg: sort_by_play_history(
                start_date=cfg_str_or_none(cfg, "start_date"),
                end_date=cfg_str_or_none(cfg, "end_date"),
                min_days_back=cfg_int(cfg, "min_days_back"),
                max_days_back=cfg_int(cfg, "max_days_back"),
                reverse=cfg_bool(cfg, "reverse", True),
            ),
            "Sorts tracks by play frequency within optional time windows",
        ),
        "by_added_at": _tf(
            lambda _ctx, cfg: sort_by_date(
                date_source="added_at",
                ascending=cfg_bool(cfg, "ascending", True),
            ),
            "Sorts tracks by date added to source playlist",
        ),
        "by_first_played": _tf(
            lambda _ctx, cfg: sort_by_date(
                date_source="first_played",
                ascending=cfg_bool(cfg, "ascending", True),
            ),
            "Sorts tracks by date first played",
        ),
        "by_last_played": _tf(
            lambda _ctx, cfg: sort_by_date(
                date_source="last_played",
                ascending=cfg_bool(cfg, "ascending", True),
            ),
            "Sorts tracks by date most recently played",
        ),
        "reverse": _tf(
            lambda _ctx, _cfg: reverse_tracks(),
            "Reverses current track order",
        ),
        "weighted_shuffle": _tf(
            lambda _ctx, cfg: weighted_shuffle(
                cfg_float(cfg, "shuffle_strength", 0.5),
            ),
            "Shuffles tracks with configurable strength (0.0=original order, 1.0=fully random)",
        ),
    },
    "selector": {
        "limit_tracks": _tf(
            lambda _ctx, cfg: select_by_method(
                cfg_int(cfg, "count", 10),
                cfg_str(cfg, "method", "first"),
            ),
            "Limits playlist to specified number of tracks",
        ),
        "percentage": _tf(
            lambda _ctx, cfg: select_by_percentage(
                percentage=cfg_float(cfg, "percentage", 0.0),
                method=cfg_str(cfg, "method", "first"),
            ),
            "Selects a percentage of tracks",
        ),
    },
}

# Combiner registry: honest list[TrackList] → TrackList semantics.
# Upstream tracklists are collected by make_combiner_node(), not by the lambda.
COMBINER_REGISTRY: dict[str, CombinerEntry] = {
    "merge_playlists": CombinerEntry(
        concatenate,
        "Combines multiple playlists into one",
    ),
    "concatenate_playlists": CombinerEntry(  # Alias — used by 3 JSON definitions
        concatenate,
        "Joins playlists in specified order",
    ),
    "interleave_playlists": CombinerEntry(
        interleave,
        "Interleaves tracks from multiple playlists",
    ),
    "intersect_playlists": CombinerEntry(
        intersect,
        "Keeps only tracks common to all input sources",
    ),
}
