"""Rich config field definitions for all workflow node types.

Single source of truth for field metadata: labels, descriptions, types,
options, defaults, and validation constraints. The frontend renders forms
directly from this data — no static schema duplication.

The registry covers every cfg["key"] and cfg.get("key") usage across
source_nodes.py, transform_definitions.py, destination_nodes.py, and
enricher_nodes.py.
"""

import functools
from typing import Literal

from attrs import define

type FieldType = Literal["string", "number", "boolean", "select"]


@define(frozen=True, slots=True)
class ConfigFieldOption:
    """A selectable option for a 'select' field."""

    value: str
    label: str
    description: str | None = None


@define(frozen=True, slots=True)
class ConfigFieldDef:
    """Rich metadata for a single config field on a workflow node."""

    key: str
    label: str
    field_type: FieldType
    required: bool = False
    description: str | None = None
    default: str | float | bool | None = None
    placeholder: str | None = None
    min: float | None = None
    max: float | None = None
    options: tuple[ConfigFieldOption, ...] = ()


# ── Shared option tuples (reused across node types) ───────────────


CONNECTOR_OPTIONS = (
    ConfigFieldOption("spotify", "Spotify", "Stream from your Spotify library"),
    ConfigFieldOption("lastfm", "Last.fm", "Scrobble data from Last.fm"),
)

# Authoritative mapping: enricher node type → metrics it provides (with UI metadata).
# Every consumer (validation, node_catalog, UI) derives from this single source.
ENRICHER_METRIC_DEFS: dict[str, tuple[ConfigFieldOption, ...]] = {
    "enricher.lastfm": (
        ConfigFieldOption(
            "lastfm_user_playcount",
            "Play Count (Last.fm)",
            "Your personal play count from Last.fm scrobbles",
        ),
        ConfigFieldOption(
            "lastfm_global_playcount",
            "Global Play Count (Last.fm)",
            "Total plays across all Last.fm users",
        ),
    ),
    "enricher.spotify": (
        ConfigFieldOption(
            "explicit_flag",
            "Explicit Flag",
            "Whether the track has explicit content (from Spotify)",
        ),
    ),
    "enricher.play_history": (
        ConfigFieldOption(
            "total_plays",
            "Total Plays",
            "Your all-time play count from internal history",
        ),
        ConfigFieldOption(
            "period_plays",
            "Period Plays",
            "Your play count within a specific time window",
        ),
        ConfigFieldOption(
            "last_played_dates",
            "Last Played Date",
            "When you most recently listened to this track",
        ),
        ConfigFieldOption(
            "first_played_dates",
            "First Played Date",
            "When you first listened to this track",
        ),
    ),
}

# Flattened union of all enricher metrics — used by filter/sorter UI dropdowns.
METRIC_OPTIONS: tuple[ConfigFieldOption, ...] = tuple(
    opt for opts in ENRICHER_METRIC_DEFS.values() for opt in opts
)

SERVICE_OPTIONS = (ConfigFieldOption("spotify", "Spotify"),)

SELECTION_METHOD_OPTIONS = (
    ConfigFieldOption("first", "First", "Take from the beginning of the list"),
    ConfigFieldOption("last", "Last", "Take from the end of the list"),
    ConfigFieldOption("random", "Random", "Take randomly from the list"),
)

PREFERENCE_STATE_OPTIONS = (
    ConfigFieldOption("star", "★ Starred", "Highly curated tracks"),
    ConfigFieldOption("yah", "Yah", "Approved tracks"),
    ConfigFieldOption("hmm", "Hmm", "Undecided — waiting for another listen"),
    ConfigFieldOption("nah", "Nah", "Rejected tracks"),
)

TAG_MATCH_MODE_OPTIONS = (
    ConfigFieldOption("any", "Any", "Keep tracks with at least one of the tags"),
    ConfigFieldOption("all", "All", "Keep tracks with every one of the tags"),
)

EXPLICIT_FILTER_OPTIONS = (
    ConfigFieldOption("explicit", "Explicit Only", "Keep only tracks marked explicit"),
    ConfigFieldOption(
        "clean", "Clean Only", "Keep only tracks without explicit content"
    ),
    ConfigFieldOption("all", "All Tracks", "Don't filter by explicit status"),
)

SORT_ORDER_OPTIONS = (
    ConfigFieldOption("true", "Highest first", "Sort from highest to lowest value"),
    ConfigFieldOption("false", "Lowest first", "Sort from lowest to highest value"),
)

DATE_SORT_ORDER_OPTIONS = (
    ConfigFieldOption("false", "Newest first", "Most recent dates first"),
    ConfigFieldOption("true", "Oldest first", "Earliest dates first"),
)

LIKED_STATUS_OPTIONS = (
    ConfigFieldOption("true", "Liked tracks", "Keep only liked/favorited tracks"),
    ConfigFieldOption("false", "Unliked tracks", "Keep only tracks you haven't liked"),
)

SORT_BY_LIKED_OPTIONS = (
    ConfigFieldOption("liked_at_desc", "Recently liked", "Most recently liked first"),
    ConfigFieldOption("liked_at_asc", "Earliest liked", "Earliest liked first"),
    ConfigFieldOption("title_asc", "Title A-Z", "Alphabetical by title"),
    ConfigFieldOption("random", "Random", "Random order"),
)

SORT_BY_PLAYED_OPTIONS = (
    ConfigFieldOption(
        "played_at_desc", "Recently played", "Most recently played first"
    ),
    ConfigFieldOption("played_at_asc", "Earliest played", "Earliest played first"),
    ConfigFieldOption("total_plays_desc", "Most played", "Highest play count first"),
    ConfigFieldOption("random", "Random", "Random order"),
)

INCLUDE_MISSING_FIELD = ConfigFieldDef(
    key="include_missing",
    label="Include Missing",
    field_type="boolean",
    description="Include tracks with no play history data",
    default=False,
)


def _date_range_fields(
    min_days_label: str = "Played Within (days)",
    max_days_label: str = "Not Played Since (days)",
) -> tuple[ConfigFieldDef, ...]:
    """Shared date-range constraint fields used by play-history filter and sorter."""
    return (
        ConfigFieldDef(
            key="min_days_back",
            label=min_days_label,
            field_type="number",
            description="Only count plays within this many recent days",
            placeholder="30",
            min=1,
        ),
        ConfigFieldDef(
            key="max_days_back",
            label=max_days_label,
            field_type="number",
            description="Only count plays older than this many days",
            min=1,
        ),
        ConfigFieldDef(
            key="start_date",
            label="Start Date",
            field_type="string",
            description="Only count plays after this date (YYYY-MM-DD)",
            placeholder="2024-01-01",
        ),
        ConfigFieldDef(
            key="end_date",
            label="End Date",
            field_type="string",
            description="Only count plays before this date (YYYY-MM-DD)",
            placeholder="2024-12-31",
        ),
    )


# ── Per-node field definitions ────────────────────────────────────
# Every registered node type with all its cfg["key"] and cfg.get("key") usage.


_NODE_CONFIG_FIELDS: dict[str, tuple[ConfigFieldDef, ...]] = {
    # === SOURCES ===
    "source.playlist": (
        ConfigFieldDef(
            key="playlist_id",
            label="Source Playlist",
            field_type="string",
            required=True,
            description="Spotify playlist URI, URL, or ID",
            placeholder="spotify:playlist:37i9dQZF1DXcBWIGoYBM5M",
        ),
        ConfigFieldDef(
            key="connector",
            label="Service",
            field_type="select",
            description="Which service to fetch the playlist from. Leave empty to read from local database.",
            options=CONNECTOR_OPTIONS,
        ),
    ),
    "source.liked_tracks": (
        ConfigFieldDef(
            key="limit",
            label="Max Tracks",
            field_type="number",
            description="Maximum number of liked tracks to retrieve",
            placeholder="500",
            min=1,
            max=10000,
        ),
        ConfigFieldDef(
            key="connector_filter",
            label="Service Filter",
            field_type="select",
            description="Only include likes from a specific service",
            options=SERVICE_OPTIONS,
        ),
        ConfigFieldDef(
            key="sort_by",
            label="Sort By",
            field_type="select",
            description="How to order the liked tracks",
            default="liked_at_desc",
            options=SORT_BY_LIKED_OPTIONS,
        ),
    ),
    "source.preferred_tracks": (
        ConfigFieldDef(
            key="state",
            label="Preference State",
            field_type="select",
            required=True,
            description="Which preference bucket to draw from",
            options=PREFERENCE_STATE_OPTIONS,
        ),
        ConfigFieldDef(
            key="limit",
            label="Max Tracks",
            field_type="number",
            description="Maximum number of tracks to retrieve",
            placeholder="500",
            min=1,
            max=10000,
        ),
    ),
    "source.played_tracks": (
        ConfigFieldDef(
            key="limit",
            label="Max Tracks",
            field_type="number",
            description="Maximum number of played tracks to retrieve",
            placeholder="500",
            min=1,
            max=10000,
        ),
        ConfigFieldDef(
            key="days_back",
            label="Days Back",
            field_type="number",
            description="Only include tracks played within this many days",
            placeholder="90",
            min=1,
        ),
        ConfigFieldDef(
            key="connector_filter",
            label="Service Filter",
            field_type="select",
            description="Only include plays from a specific service",
            options=SERVICE_OPTIONS,
        ),
        ConfigFieldDef(
            key="sort_by",
            label="Sort By",
            field_type="select",
            description="How to order the played tracks",
            default="played_at_desc",
            options=SORT_BY_PLAYED_OPTIONS,
        ),
    ),
    # === ENRICHERS ===
    "enricher.lastfm": (),
    "enricher.spotify": (),
    "enricher.play_history": (),
    "enricher.preferences": (),
    "enricher.tags": (),
    "enricher.spotify_liked_status": (),
    # === FILTERS ===
    "filter.deduplicate": (),
    "filter.by_release_date": (
        ConfigFieldDef(
            key="min_age_days",
            label="Minimum Age (days)",
            field_type="number",
            description="Only keep tracks released at least this many days ago",
            placeholder="30",
            min=0,
        ),
        ConfigFieldDef(
            key="max_age_days",
            label="Maximum Age (days)",
            field_type="number",
            description="Only keep tracks released within this many days",
            placeholder="365",
            min=0,
        ),
    ),
    "filter.by_tracks": (
        ConfigFieldDef(
            key="exclusion_source",
            label="Exclude From",
            field_type="string",
            required=True,
            description="Task ID whose tracks will be removed from this list",
            placeholder="source_liked_1",
        ),
    ),
    "filter.by_artists": (
        ConfigFieldDef(
            key="exclusion_source",
            label="Exclude From",
            field_type="string",
            required=True,
            description="Task ID whose artists will be removed from this list",
            placeholder="source_liked_1",
        ),
        ConfigFieldDef(
            key="exclude_all_artists",
            label="Exclude All Artists",
            field_type="boolean",
            description="When enabled, excludes tracks if any artist matches (not just primary)",
            default=False,
        ),
    ),
    "filter.by_metric": (
        ConfigFieldDef(
            key="metric_name",
            label="Metric",
            field_type="select",
            required=True,
            description="Which metric to filter by (requires matching enricher upstream)",
            options=METRIC_OPTIONS,
        ),
        ConfigFieldDef(
            key="min_value",
            label="Minimum",
            field_type="number",
            description="Keep tracks with metric value at or above this",
            placeholder="0",
        ),
        ConfigFieldDef(
            key="max_value",
            label="Maximum",
            field_type="number",
            description="Keep tracks with metric value at or below this",
            placeholder="No limit",
        ),
        INCLUDE_MISSING_FIELD,
    ),
    "filter.by_duration": (
        ConfigFieldDef(
            key="min_ms",
            label="Minimum Duration (ms)",
            field_type="number",
            description="Keep tracks longer than this (in milliseconds)",
            placeholder="30000",
            min=0,
        ),
        ConfigFieldDef(
            key="max_ms",
            label="Maximum Duration (ms)",
            field_type="number",
            description="Keep tracks shorter than this (in milliseconds)",
            placeholder="600000",
            min=0,
        ),
        INCLUDE_MISSING_FIELD,
    ),
    "filter.by_liked_status": (
        ConfigFieldDef(
            key="service",
            label="Service",
            field_type="select",
            required=True,
            description="Which service's liked status to check",
            options=SERVICE_OPTIONS,
        ),
        ConfigFieldDef(
            key="is_liked",
            label="Keep",
            field_type="select",
            description="Whether to keep liked or unliked tracks",
            default="true",
            options=LIKED_STATUS_OPTIONS,
        ),
    ),
    "filter.by_explicit": (
        ConfigFieldDef(
            key="keep",
            label="Content Filter",
            field_type="select",
            description="Filter tracks by explicit content status",
            default="all",
            options=EXPLICIT_FILTER_OPTIONS,
        ),
    ),
    "filter.by_play_history": (
        ConfigFieldDef(
            key="min_plays",
            label="Minimum Plays",
            field_type="number",
            description="Keep tracks played at least this many times",
            placeholder="1",
            min=0,
        ),
        ConfigFieldDef(
            key="max_plays",
            label="Maximum Plays",
            field_type="number",
            description="Keep tracks played at most this many times",
            min=0,
        ),
        *_date_range_fields(),
        INCLUDE_MISSING_FIELD,
    ),
    "filter.by_preference": (
        ConfigFieldDef(
            key="include",
            label="Include States",
            field_type="string",
            description=(
                "Comma-separated preference states to KEEP "
                "(e.g. 'star' or 'yah,star'). Pass this OR exclude, not both."
            ),
            placeholder="star",
        ),
        ConfigFieldDef(
            key="exclude",
            label="Exclude States",
            field_type="string",
            description=(
                "Comma-separated preference states to REMOVE (e.g. 'nah'). "
                "Unrated tracks are always kept in exclude mode."
            ),
            placeholder="nah",
        ),
    ),
    "filter.by_tag": (
        ConfigFieldDef(
            key="tags",
            label="Tags",
            field_type="string",
            required=True,
            description="Comma-separated tags to match (e.g. 'mood:chill,energy:low')",
            placeholder="mood:chill",
        ),
        ConfigFieldDef(
            key="match_mode",
            label="Match Mode",
            field_type="select",
            description="Require any or all tags to be present",
            default="any",
            options=TAG_MATCH_MODE_OPTIONS,
        ),
    ),
    "filter.by_tag_namespace": (
        ConfigFieldDef(
            key="namespace",
            label="Namespace",
            field_type="string",
            required=True,
            description="Namespace to match (e.g. 'mood', 'context')",
            placeholder="mood",
        ),
        ConfigFieldDef(
            key="values",
            label="Values",
            field_type="string",
            description=(
                "Optional comma-separated values within the namespace "
                "(e.g. 'chill,melancholy'). Empty means any value."
            ),
            placeholder="chill,melancholy",
        ),
    ),
    # === SORTERS ===
    "sorter.by_metric": (
        ConfigFieldDef(
            key="metric_name",
            label="Metric",
            field_type="select",
            required=True,
            description="Which metric to sort by (requires matching enricher upstream)",
            options=METRIC_OPTIONS,
        ),
        ConfigFieldDef(
            key="reverse",
            label="Sort Order",
            field_type="select",
            description="Direction of the sort",
            default="true",
            options=SORT_ORDER_OPTIONS,
        ),
    ),
    "sorter.by_release_date": (
        ConfigFieldDef(
            key="reverse",
            label="Sort Order",
            field_type="select",
            description="Direction of the sort",
            default="false",
            options=DATE_SORT_ORDER_OPTIONS,
        ),
    ),
    "sorter.by_preference": (
        ConfigFieldDef(
            key="reverse",
            label="Sort Order",
            field_type="select",
            description="Direction of the sort (default puts starred first)",
            default="true",
            options=SORT_ORDER_OPTIONS,
        ),
    ),
    "sorter.by_play_history": (
        ConfigFieldDef(
            key="reverse",
            label="Sort Order",
            field_type="select",
            description="Direction of the sort",
            default="true",
            options=SORT_ORDER_OPTIONS,
        ),
        *_date_range_fields(
            min_days_label="Within (days)",
            max_days_label="Older Than (days)",
        ),
    ),
    "sorter.by_added_at": (
        ConfigFieldDef(
            key="ascending",
            label="Sort Order",
            field_type="select",
            description="Direction of the sort",
            default="true",
            options=DATE_SORT_ORDER_OPTIONS,
        ),
    ),
    "sorter.by_first_played": (
        ConfigFieldDef(
            key="ascending",
            label="Sort Order",
            field_type="select",
            description="Direction of the sort",
            default="true",
            options=DATE_SORT_ORDER_OPTIONS,
        ),
    ),
    "sorter.by_last_played": (
        ConfigFieldDef(
            key="ascending",
            label="Sort Order",
            field_type="select",
            description="Direction of the sort",
            default="true",
            options=DATE_SORT_ORDER_OPTIONS,
        ),
    ),
    "sorter.reverse": (),
    "sorter.weighted_shuffle": (
        ConfigFieldDef(
            key="shuffle_strength",
            label="Shuffle Strength",
            field_type="number",
            description="0.0 keeps original order, 1.0 is fully random",
            default=0.5,
            min=0.0,
            max=1.0,
            placeholder="0.5",
        ),
    ),
    # === SELECTORS ===
    "selector.limit_tracks": (
        ConfigFieldDef(
            key="count",
            label="Track Count",
            field_type="number",
            description="How many tracks to keep",
            default=10,
            placeholder="10",
            min=1,
        ),
        ConfigFieldDef(
            key="method",
            label="Selection Method",
            field_type="select",
            description="Which tracks to keep from the list",
            default="first",
            options=SELECTION_METHOD_OPTIONS,
        ),
    ),
    "selector.percentage": (
        ConfigFieldDef(
            key="percentage",
            label="Percentage",
            field_type="number",
            required=True,
            description="What percentage of tracks to keep",
            placeholder="50",
            min=1,
            max=100,
        ),
        ConfigFieldDef(
            key="method",
            label="Selection Method",
            field_type="select",
            description="Which tracks to keep from the list",
            default="first",
            options=SELECTION_METHOD_OPTIONS,
        ),
    ),
    # === COMBINERS ===
    "combiner.merge_playlists": (),
    "combiner.concatenate_playlists": (),
    "combiner.interleave_playlists": (),
    "combiner.intersect_playlists": (),
    # === DESTINATIONS ===
    "destination.create_playlist": (
        ConfigFieldDef(
            key="name",
            label="Playlist Name",
            field_type="string",
            required=True,
            description="Name for the new playlist",
            placeholder="My Workflow Playlist",
        ),
        ConfigFieldDef(
            key="description",
            label="Description",
            field_type="string",
            description="Optional description for the playlist",
            placeholder="Created by Mixd",
        ),
        ConfigFieldDef(
            key="connector",
            label="Service",
            field_type="select",
            description="Also create on this service (leave empty for local only)",
            options=CONNECTOR_OPTIONS,
        ),
    ),
    "destination.update_playlist": (
        ConfigFieldDef(
            key="playlist_id",
            label="Target Playlist",
            field_type="string",
            required=True,
            description="ID of the playlist to update (canonical or connector ID)",
            placeholder="spotify:playlist:37i9dQZF1DXcBWIGoYBM5M",
        ),
        ConfigFieldDef(
            key="connector",
            label="Service",
            field_type="select",
            description="Which service the playlist ID belongs to (leave empty for local)",
            options=CONNECTOR_OPTIONS,
        ),
        ConfigFieldDef(
            key="append",
            label="Append Mode",
            field_type="boolean",
            description="Add tracks to existing playlist instead of replacing all tracks",
            default=False,
        ),
        ConfigFieldDef(
            key="name",
            label="New Name",
            field_type="string",
            description="Optionally rename the playlist",
        ),
        ConfigFieldDef(
            key="description",
            label="New Description",
            field_type="string",
            description="Optionally update the playlist description",
        ),
    ),
}


def get_node_config_fields() -> dict[str, tuple[ConfigFieldDef, ...]]:
    """Public accessor for the rich node config field registry."""
    return _NODE_CONFIG_FIELDS


@functools.cache
def get_enricher_metric_names() -> dict[str, frozenset[str]]:
    """Derive enricher → metric name sets from the canonical mapping.

    Cached — ENRICHER_METRIC_DEFS is a module-level constant.
    Returns frozensets for hashability (functools.cache requirement).
    """
    return {
        enricher: frozenset(opt.value for opt in opts)
        for enricher, opts in ENRICHER_METRIC_DEFS.items()
    }


def get_enricher_attributes(enricher_type: str) -> list[str]:
    """Get the attribute name list for an enricher (for node_catalog registration)."""
    return [opt.value for opt in ENRICHER_METRIC_DEFS.get(enricher_type, ())]
