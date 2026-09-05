"""Rich config field definitions for all workflow node types.

Single source of truth for field metadata: labels, descriptions, types,
options, defaults, and validation constraints. The frontend renders forms
directly from this data — no static schema duplication.

Options that name a connector or a connector metric are derived at first use
from the connector catalog and the metric registry, so the registry stays the
one place a connector or metric is declared.

The registry covers every cfg["key"] and cfg.get("key") usage across
source.py, transform_definitions.py, destination.py, enricher.py, and
factories.py, plus the executor's ``primary_input`` read. A field's
``default`` is applied once, by the executor through
``apply_declared_defaults``; node code reads the key without restating it.
"""

from collections.abc import Mapping
import functools
from operator import attrgetter
from typing import Literal

from attrs import define

from src.application.use_cases._shared.connector_catalog import (
    default_connector_catalog,
)
from src.application.use_cases._shared.metric_config import default_metric_config
from src.config.constants import BusinessLimits
from src.domain.entities.connector import Capability, ConnectorDescriptor
from src.domain.entities.shared import JsonValue

from .registry import list_nodes

type FieldType = Literal[
    "string", "number", "boolean", "select", "multi_select", "task_ref"
]


@define(frozen=True, slots=True)
class ConfigFieldOption:
    """A selectable option for a 'select' or 'multi_select' field."""

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
    default: str | float | bool | tuple[str, ...] | None = None
    placeholder: str | None = None
    min: float | None = None
    max: float | None = None
    options: tuple[ConfigFieldOption, ...] = ()

    def json_default(self) -> str | float | bool | list[str] | None:
        """The declared default as a JSON value: tuples become lists."""
        return list(self.default) if isinstance(self.default, tuple) else self.default


def format_bound(value: float) -> str:
    """Render a ``min``/``max`` bound without an exponent.

    ``1000000`` stays ``1000000`` (``:g`` would give ``1e+06``); ``0.5`` stays
    ``0.5``. Every surface that prints a bound — validator warnings, the chat
    node catalog, the JSON schema prose — goes through here.
    """
    return str(int(value)) if value == int(value) else f"{value:g}"


def is_unset(field: ConfigFieldDef, value: JsonValue) -> bool:
    """True when a present config value should count as absent.

    JSON ``null`` is absent for every field; an empty list is absent for a
    ``multi_select``. The one rule that both ``apply_declared_defaults`` and
    the validator consult, so a value that falls back to the declared default
    at run time is judged the same way at save time.
    """
    if value is None:
        return True
    return field.field_type == "multi_select" and value == []


# ── Shared option tuples (reused across node types) ───────────────


# Descriptions for connector-service pickers. The connector registry carries no
# field for this copy, so it lives here; connectors without an entry fall back
# to their display name.
_CONNECTOR_OPTION_DESCRIPTIONS: dict[str, str] = {
    "spotify": "Stream from your Spotify library",
}


def _descriptors_with(capability: Capability) -> list[ConnectorDescriptor]:
    """Registered connectors declaring a capability, ordered by name."""
    return sorted(
        (
            descriptor
            for descriptor in default_connector_catalog().list_descriptors()
            if capability in descriptor.capabilities
        ),
        key=attrgetter("name"),
    )


def _connector_options() -> tuple[ConfigFieldOption, ...]:
    """Services a workflow can read a playlist from or write one to."""
    return tuple(
        ConfigFieldOption(
            descriptor.name,
            descriptor.display_name,
            _CONNECTOR_OPTION_DESCRIPTIONS.get(
                descriptor.name, descriptor.display_name
            ),
        )
        for descriptor in _descriptors_with("playlist_sync")
    )


def _service_options() -> tuple[ConfigFieldOption, ...]:
    """Services whose liked tracks mixd imports — the likes/service filters."""
    return tuple(
        ConfigFieldOption(descriptor.name, descriptor.display_name)
        for descriptor in _descriptors_with("likes_import")
    )


# Metrics enricher.play_history emits, sourced from mixd's own listening
# history rather than from a connector — so they are declared here, not in the
# metric registry.
_PLAY_HISTORY_METRIC_DEFS: tuple[ConfigFieldOption, ...] = (
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
)

# Metrics enricher.play_history emits when its `metrics` config is omitted.
# Single source of truth shared by the enrichment-config builder (factories.py)
# and the dependency validator (validation.py): the latter must know that
# first_played_dates is NOT in the default set, so a consumer needing it requires
# an enricher explicitly configured to emit it.
DEFAULT_PLAY_HISTORY_METRICS: tuple[str, ...] = ("total_plays", "last_played_dates")


@functools.cache
def get_enricher_metric_defs() -> dict[str, tuple[ConfigFieldOption, ...]]:
    """Authoritative mapping: enricher node type → metrics it provides.

    Connector-backed enrichers derive from the metric registry, so declaring a
    new ``MetricSpec`` on a connector surfaces it in the UI with no edit here.
    Every consumer (validation, catalog, UI) reads this one mapping.
    """
    metric_config = default_metric_config()
    defs: dict[str, tuple[ConfigFieldOption, ...]] = {
        f"enricher.{connector}": tuple(
            ConfigFieldOption(
                metric,
                metric_config.get_metric_label(metric),
                metric_config.get_metric_description(metric) or None,
            )
            for metric in metrics
        )
        for connector, metrics in sorted(
            metric_config.get_all_connectors_metrics().items()
        )
        if metrics
    }
    defs["enricher.play_history"] = _PLAY_HISTORY_METRIC_DEFS
    return defs


def get_metric_options() -> tuple[ConfigFieldOption, ...]:
    """Flattened union of all enricher metrics — filter/sorter UI dropdowns."""
    return tuple(opt for opts in get_enricher_metric_defs().values() for opt in opts)


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

DATE_SORT_ORDER_FIELD = ConfigFieldDef(
    key="ascending",
    label="Oldest First",
    field_type="boolean",
    description="true = oldest dates first, false = newest first",
    default=True,
)

# Sort direction for the value-ranked sorters (metric, play count, preference).
HIGHEST_FIRST_FIELD = ConfigFieldDef(
    key="reverse",
    label="Highest First",
    field_type="boolean",
    description="true = highest value first, false = lowest first",
    default=True,
)

# Which upstream a multi-input node reads as its own tracklist. The executor
# reads it straight from the saved config (no default), so every non-source
# node declares it and validation checks it names a real upstream.
PRIMARY_INPUT_FIELD = ConfigFieldDef(
    key="primary_input",
    label="Primary Input",
    field_type="task_ref",
    description=(
        "Which upstream feeds this node when it has more than one; "
        "defaults to the first"
    ),
)

DEDUPLICATE_FIELD = ConfigFieldDef(
    key="deduplicate",
    label="Remove Duplicates",
    field_type="boolean",
    description="Drop tracks that appear in more than one input",
    default=False,
)


def _play_count_fields() -> tuple[ConfigFieldDef, ...]:
    """Shared play-count bounds used by both play-history filters."""
    return (
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
    )


def _date_range_fields(
    min_days_label: str = "Not Played In (days)",
    max_days_label: str = "Played Within (days)",
) -> tuple[ConfigFieldDef, ...]:
    """Shared date-range constraint fields used by play-history filter and sorter.

    Labels follow the runtime in ``calculate_time_window``: ``not_played_in_days``
    is the *minimum age* (keeps plays older than N days — i.e. excludes the
    recent N), and ``played_within_days`` is the *maximum age* (keeps plays within
    the last N days). Defaults read in the last-played sense; callers override
    the wording for other date sources.
    """
    return (
        ConfigFieldDef(
            key="not_played_in_days",
            label=min_days_label,
            field_type="number",
            description="Only keep plays older than this many days",
            placeholder="30",
            min=1,
        ),
        ConfigFieldDef(
            key="played_within_days",
            label=max_days_label,
            field_type="number",
            description="Only keep plays within this many recent days",
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


def _require_options_for_required_selects(
    registry: Mapping[str, tuple[ConfigFieldDef, ...]],
) -> None:
    """Refuse a registry whose required select has nothing to select.

    Registry-derived option lists come from the connector catalog; an empty
    one on a required field would reach the editor as an unsatisfiable
    dropdown and the model as an enum with no members.

    Raises:
        RuntimeError: Naming the node and field with zero options.
    """
    for node_id, fields in registry.items():
        for field in fields:
            if (
                field.field_type in ("select", "multi_select")
                and field.required
                and not field.options
            ):
                raise RuntimeError(
                    f"{node_id}.{field.key}: required select has no options"
                )


def _build_node_config_fields() -> dict[str, tuple[ConfigFieldDef, ...]]:
    """Assemble the per-node field registry, resolving registry-derived options.

    Uncached so tests can exercise the required-select check with patched
    option sources; production reads go through ``get_node_config_fields``.
    """
    connector_options = _connector_options()
    service_options = _service_options()
    metric_options = get_metric_options()

    registry: dict[str, tuple[ConfigFieldDef, ...]] = {
        # === SOURCES ===
        # KNOWN TERMINOLOGY EXCEPTION — fix eventually.
        # ``playlist_id`` here is polymorphic: when ``connector`` is empty it is
        # the canonical mixd ``playlists.id`` (UUID); when ``connector`` is set
        # it carries the *connector identifier* (Spotify base62 etc.). The
        # project rule is one name per concept — split this into two fields
        # (``playlist_id`` canonical-only + ``connector_playlist_identifier``)
        # when the editor/seed JSONs are next touched. See the matching note
        # in ``application/workflows/config_accessors.py``.
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
                options=connector_options,
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
                max=BusinessLimits.MAX_USER_LIMIT,
            ),
            ConfigFieldDef(
                key="connector_filter",
                label="Service Filter",
                field_type="select",
                description="Only include likes from a specific service",
                options=service_options,
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
                max=BusinessLimits.MAX_USER_LIMIT,
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
                max=BusinessLimits.MAX_USER_LIMIT,
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
                options=service_options,
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
        "enricher.play_history": (
            ConfigFieldDef(
                key="metrics",
                label="Metrics",
                field_type="multi_select",
                description="Which play-history metrics to attach to each track",
                default=DEFAULT_PLAY_HISTORY_METRICS,
                options=_PLAY_HISTORY_METRIC_DEFS,
            ),
            ConfigFieldDef(
                key="period_days",
                label="Period (days)",
                field_type="number",
                description=(
                    "Window for the period_plays metric; only applies when "
                    "period_plays is selected"
                ),
                placeholder="30",
                min=1,
            ),
        ),
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
        "filter.by_release_year": (
            ConfigFieldDef(
                key="min_year",
                label="From Year",
                field_type="number",
                description="Earliest release year to keep (inclusive)",
                placeholder="2010",
                min=1900,
                max=2100,
            ),
            ConfigFieldDef(
                key="max_year",
                label="To Year",
                field_type="number",
                description="Latest release year to keep (inclusive)",
                placeholder="2019",
                min=1900,
                max=2100,
            ),
            ConfigFieldDef(
                key="include_missing",
                label="Include Missing",
                field_type="boolean",
                description="Include tracks with no release date",
                default=False,
            ),
        ),
        "filter.by_tracks": (
            ConfigFieldDef(
                key="exclusion_source",
                label="Exclude From",
                field_type="task_ref",
                required=True,
                description="Upstream task whose tracks will be removed from this list",
            ),
        ),
        "filter.by_artists": (
            ConfigFieldDef(
                key="exclusion_source",
                label="Exclude From",
                field_type="task_ref",
                required=True,
                description="Upstream task whose artists will be removed from this list",
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
                options=metric_options,
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
                options=service_options,
            ),
            ConfigFieldDef(
                key="is_liked",
                label="Keep Liked",
                field_type="boolean",
                description="true = keep only liked tracks, false = keep only unliked",
                default=True,
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
            *_play_count_fields(),
            *_date_range_fields(),
            INCLUDE_MISSING_FIELD,
        ),
        "filter.by_first_played_date": (
            *_play_count_fields(),
            *_date_range_fields(
                min_days_label="First Played Before (days ago)",
                max_days_label="First Played Within (days)",
            ),
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
                options=metric_options,
            ),
            HIGHEST_FIRST_FIELD,
        ),
        "sorter.by_release_date": (
            ConfigFieldDef(
                key="reverse",
                label="Newest First",
                field_type="boolean",
                description="true = newest releases first, false = oldest first",
                default=False,
            ),
        ),
        "sorter.by_preference": (
            ConfigFieldDef(
                key="reverse",
                label="Strongest First",
                field_type="boolean",
                description="true = starred first, false = weakest preference first",
                default=True,
            ),
        ),
        "sorter.by_play_history": (
            HIGHEST_FIRST_FIELD,
            *_date_range_fields(
                min_days_label="Older Than (days)",
                max_days_label="Within (days)",
            ),
        ),
        "sorter.by_added_at": (DATE_SORT_ORDER_FIELD,),
        "sorter.by_first_played": (DATE_SORT_ORDER_FIELD,),
        "sorter.by_last_played": (DATE_SORT_ORDER_FIELD,),
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
        "combiner.merge_playlists": (DEDUPLICATE_FIELD,),
        "combiner.concatenate_playlists": (DEDUPLICATE_FIELD,),
        "combiner.interleave_playlists": (DEDUPLICATE_FIELD,),
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
                default="Created by Mixd",
                placeholder="Created by Mixd",
            ),
            ConfigFieldDef(
                key="connector",
                label="Service",
                field_type="select",
                description="Also create on this service (leave empty for local only)",
                options=connector_options,
            ),
        ),
        # KNOWN TERMINOLOGY EXCEPTION — see source.playlist above.
        # Same polymorphic ``playlist_id`` shape: canonical UUID without
        # ``connector``, connector identifier (Spotify base62 etc.) with it.
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
                options=connector_options,
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
    # Every node that takes input can name which upstream is its primary one.
    # The registry owns the category, so the append follows it rather than the
    # node id prefix.
    categories = {nid: meta["category"] for nid, meta in list_nodes().items()}
    for node_id, fields in registry.items():
        if node_id in categories and categories[node_id] != "source":
            registry[node_id] = (*fields, PRIMARY_INPUT_FIELD)
    _require_options_for_required_selects(registry)
    return registry


@functools.cache
def get_node_config_fields() -> dict[str, tuple[ConfigFieldDef, ...]]:
    """Public accessor for the rich node config field registry.

    Cached: the connector catalog and metric registry it reads are fixed for
    the life of the process, so the registry is built once on first use.
    """
    return _build_node_config_fields()


def apply_declared_defaults(
    node_type: str, config: Mapping[str, JsonValue]
) -> dict[str, JsonValue]:
    """Fill absent config keys with the defaults the node's fields declare.

    The one place a declared ``default`` becomes a runtime value: the executor
    calls this before a node runs, so node code reads keys without restating
    the default. A key is absent when it is missing or when ``is_unset`` says
    so (``null``, or ``[]`` on a multi_select); such keys are dropped before
    the defaults fill in. Every other present value wins, falsy or not.
    """
    merged = dict(config)
    for field in get_node_config_fields().get(node_type, ()):
        if field.key in merged and is_unset(field, merged[field.key]):
            del merged[field.key]
        if field.key not in merged and field.default is not None:
            merged[field.key] = field.json_default()
    return merged


@functools.cache
def get_enricher_metric_names() -> dict[str, frozenset[str]]:
    """Derive enricher → metric name sets from the canonical mapping.

    Returns frozensets for hashability (functools.cache requirement).
    """
    return {
        enricher: frozenset(opt.value for opt in opts)
        for enricher, opts in get_enricher_metric_defs().items()
    }
