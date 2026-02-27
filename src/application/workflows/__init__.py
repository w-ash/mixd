"""Workflow orchestration system with node-based transformation pipeline."""

from src.config import get_logger

# Force eager registration of all nodes to ensure registry completeness
# This statement is key for registry population
from . import node_catalog  # pyright: ignore[reportUnusedImport]
from .node_context import NodeContext

# Factory tools for creating nodes programmatically
from .node_factories import (
    create_destination_node,
    create_enricher_node,
    make_node,
)
from .node_registry import get_node, node

# Workflow execution
from .prefect import run_workflow
from .registry_validation import validate_registry

# Validate at module load time to catch issues early
try:  # noqa: RUF067
    _success, _message = validate_registry()
except Exception:
    _logger = get_logger(__name__)
    _logger.opt(exception=True).error(
        "Node registry validation failed — workflow nodes may be missing"
    )
    raise

# Export clean public API
__all__ = [
    "NodeContext",
    "create_destination_node",
    "create_enricher_node",
    "get_node",
    "make_node",
    "node",
    "run_workflow",
    "validate_registry",
]
