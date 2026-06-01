"""Workflow orchestration system with node-based transformation pipeline."""

import importlib

from .definition.loader import list_workflow_defs, load_workflow_def
from .definition.validation import (
    ConnectorNotAvailableError,
    extract_required_connectors,
    validate_connector_availability,
    validate_workflow_def,
)
from .nodes.execution_context import NodeContext

# Factory tools for creating nodes programmatically
from .nodes.factories import (
    build_external_enrichment_config,
    create_enricher_node,
    make_combiner_node,
    make_node,
)
from .nodes.registry import get_node, node
from .nodes.registry_validation import validate_registry

# NOTE: `run_workflow` is intentionally NOT re-exported here — importing it
# eagerly would pull the executor + connector import graph into uvicorn startup.
# Callers that need it import
# `from src.application.workflows.engine.executor import run_workflow` explicitly
# (use cases already do this lazily inside their .execute()).
# Concurrency is enforced at the DB (uq_workflow_runs_active partial unique
# index); the former in-process guard module has been removed.

# Eagerly import the node catalog for its @node registration side effects, so
# get_node() resolves every node type once this package is imported. Done via
# import_module (not `from . import catalog`) so a pure side-effect import needs
# no unused-binding suppression.
importlib.import_module(f"{__name__}.nodes.catalog")

# Export clean public API
__all__ = [
    "ConnectorNotAvailableError",
    "NodeContext",
    "build_external_enrichment_config",
    "create_enricher_node",
    "extract_required_connectors",
    "get_node",
    "list_workflow_defs",
    "load_workflow_def",
    "make_combiner_node",
    "make_node",
    "node",
    "validate_connector_availability",
    "validate_registry",
    "validate_workflow_def",
]
