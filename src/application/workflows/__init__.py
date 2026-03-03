"""Workflow orchestration system with node-based transformation pipeline."""

# Force eager registration of all nodes to ensure registry completeness
# This statement is key for registry population
from . import node_catalog  # pyright: ignore[reportUnusedImport]
from .node_context import NodeContext

# Factory tools for creating nodes programmatically
from .node_factories import (
    build_external_enrichment_config,
    create_enricher_node,
    make_combiner_node,
    make_node,
)
from .node_registry import get_node, node

# Workflow execution
from .prefect import run_workflow
from .registry_validation import validate_registry

# Export clean public API
__all__ = [
    "NodeContext",
    "build_external_enrichment_config",
    "create_enricher_node",
    "get_node",
    "make_combiner_node",
    "make_node",
    "node",
    "run_workflow",
    "validate_registry",
]
