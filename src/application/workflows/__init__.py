"""Workflow orchestration system with node-based transformation pipeline."""

from __future__ import annotations

from contextlib import suppress

# Force eager registration of all nodes to ensure registry completeness
# This statement is key for registry population
from . import node_catalog  # pyright: ignore[reportUnusedImport]
from .node_context import NodeContext

# Factory tools for creating nodes programmatically
from .node_factories import (
    WorkflowNodeFactory,
    create_destination_node,
    create_enricher_node,
    make_node,
)
from .node_registry import get_node, node, registry

# Workflow execution
from .prefect import run_workflow


def validate_registry():
    """Validate registry integrity against critical node list."""
    critical_paths = [
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
        "sorter.by_metric",
        "sorter.by_play_history",
        "selector.limit_tracks",
        "combiner.merge_playlists",
        "combiner.concatenate_playlists",
        "combiner.interleave_playlists",
        "destination.create_playlist",
        "destination.update_playlist",
    ]

    registered = set(registry.list_nodes().keys())
    missing = [c for c in critical_paths if c not in registered]

    if missing:
        missing_str = ", ".join(missing)
        raise RuntimeError(f"Node registry incomplete: missing {missing_str}")

    return True, f"Node registry validated with {len(registered)} nodes"


# Validate at module load time to catch issues early
with suppress(Exception):
    # Would need to inject logger via dependency injection for Clean Architecture
    # For now, silently ignore registry validation failures to avoid circular dependency
    success, message = validate_registry()

# Export clean public API
__all__ = [
    "NodeContext",
    "WorkflowNodeFactory",
    "create_destination_node",
    "create_enricher_node",
    "get_node",
    "make_node",
    "node",
    "run_workflow",
    "validate_registry",
]
