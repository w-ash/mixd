"""Registry validation for workflow node completeness.

Validates that all critical workflow nodes are registered at module load time,
providing clear error messages when node registration fails.
"""

from src.config import get_logger

from .node_registry import registry

logger = get_logger(__name__)

# Critical nodes that must be registered for workflows to function
CRITICAL_NODE_PATHS = [
    "source.playlist",
    "enricher.lastfm",
    "enricher.spotify",
    "enricher.play_history",
    "filter.deduplicate",
    "filter.by_release_date",
    "filter.by_tracks",
    "filter.by_artists",
    "filter.by_metric",
    "filter.by_play_history",
    "filter.by_duration",
    "filter.by_liked_status",
    "filter.by_explicit",
    "sorter.by_metric",
    "sorter.by_play_history",
    "sorter.by_added_at",
    "sorter.by_first_played",
    "sorter.by_last_played",
    "sorter.reverse",
    "selector.limit_tracks",
    "selector.percentage",
    "combiner.merge_playlists",
    "combiner.concatenate_playlists",
    "combiner.interleave_playlists",
    "combiner.intersect_playlists",
    "destination.create_playlist",
    "destination.update_playlist",
]


def validate_registry():
    """Validate registry integrity against critical node list."""
    registered = set(registry.list_nodes().keys())
    missing = [c for c in CRITICAL_NODE_PATHS if c not in registered]

    if missing:
        missing_str = ", ".join(missing)
        raise RuntimeError(f"Node registry incomplete: missing {missing_str}")

    return True, f"Node registry validated with {len(registered)} nodes"
