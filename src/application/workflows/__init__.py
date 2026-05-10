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
from .registry_validation import validate_registry

# Concurrency guard — pure asyncio, no Prefect dependency.
# `run_workflow` is intentionally NOT re-exported here: importing it would
# pull in the Prefect 3 dependency tree at uvicorn startup. Callers that
# need it must `from src.application.workflows.prefect import run_workflow`
# explicitly (use cases already do this lazily inside their .execute()).
from .run_guard import WorkflowAlreadyRunningError, is_workflow_running
from .validation import (
    ConnectorNotAvailableError,
    extract_required_connectors,
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
    "validate_connector_availability",
    "validate_registry",
    "validate_workflow_def",
]
