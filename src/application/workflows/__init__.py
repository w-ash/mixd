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
from .prefect import WorkflowAlreadyRunningError, is_workflow_running, run_workflow
from .registry_validation import validate_registry
from .validation import (
    ConnectorNotAvailableError,
    extract_required_connectors,
    topological_sort,
    validate_connector_availability,
    validate_workflow_def,
)
from .workflow_loader import list_workflow_defs, load_workflow_def

# Export clean public API
__all__ = [
    "ConnectorNotAvailableError",
    "NodeContext",
    "WorkflowAlreadyRunningError",
    "build_external_enrichment_config",
    "create_enricher_node",
    "extract_required_connectors",
    "get_node",
    "is_workflow_running",
    "list_workflow_defs",
    "load_workflow_def",
    "make_combiner_node",
    "make_node",
    "node",
    "run_workflow",
    "topological_sort",
    "validate_connector_availability",
    "validate_registry",
    "validate_workflow_def",
]
