"""
Node registry system for workflow orchestration.

This module provides a centralized registry with a clean, declarative API for
node registration and discovery. It serves as the connection point between
workflow definitions and node implementations.
"""

# Legitimate Any: use case results, OperationResult metadata, metric values

from collections.abc import Awaitable, Callable, Mapping
from typing import NotRequired, TypedDict, cast, get_args

from src.application.workflows.protocols import NodeResult
from src.config.constants import NodeType
from src.domain.entities.shared import JsonValue

# Derived from the NodeType alias so the two can never drift. The cast to
# object is what keeps the PEP 695 ``__value__`` (typed Any) out of strict
# type checking.
_VALID_CATEGORIES: frozenset[NodeType] = frozenset(
    get_args(cast("object", NodeType.__value__))
)

# First param is the workflow execution context (heterogeneous dict, narrowed via NodeContext).
# Second param is workflow config (validated JSON).
type NodeFn = Callable[
    [dict[str, object], Mapping[str, JsonValue]], Awaitable[NodeResult]
]


class NodeMetadata(TypedDict):
    """Type-safe node metadata.

    The ``requires_*`` / ``*_from_config`` keys declare enricher-consumer
    dependencies for the workflow validator; ``registry_validation`` checks
    them against the enricher catalog and each node's declared config fields.
    """

    id: str
    description: str
    category: NodeType
    input_type: NotRequired[str]
    output_type: NotRequired[str]
    required_connectors: NotRequired[list[str]]
    # Consumer nodes: the enricher type that must run upstream, and (when the
    # enricher only emits it on request) the metric that enricher must be
    # configured to produce.
    requires_enricher: NotRequired[str]
    requires_metric: NotRequired[str]
    # Consumer nodes whose required metric is named by a config key.
    metric_from_config: NotRequired[str]
    # Enricher nodes whose emitted metric set is chosen by a config key.
    emits_metrics_from_config: NotRequired[str]
    # Enricher nodes: config key -> metric that key only takes effect with.
    metric_config_corequisites: NotRequired[dict[str, str]]


# Singleton registry using a class-based pattern
class NodeRegistry:
    """Registry for workflow nodes with simplified discovery."""

    def __init__(self) -> None:
        self._registry: dict[str, tuple[NodeFn, NodeMetadata]] = {}

    def node(
        self,
        node_id: str,
        *,
        description: str = "",
        input_type: str | None = None,
        output_type: str | None = None,
        category: NodeType | None = None,
        required_connectors: list[str] | None = None,
        requires_enricher: str | None = None,
        requires_metric: str | None = None,
        metric_from_config: str | None = None,
        emits_metrics_from_config: str | None = None,
        metric_config_corequisites: dict[str, str] | None = None,
    ) -> Callable[[NodeFn], NodeFn]:
        """Register a node with the registry.

        Args:
            node_id: Unique identifier (e.g., "source.playlist")
            description: Human-readable description
            input_type: Type of input the node expects
            output_type: Type of output the node produces
            category: Node category (source, filter, etc.)
            required_connectors: External service connectors this node needs at runtime
            requires_enricher: Enricher node type that must run upstream
            requires_metric: Metric the upstream enricher must be configured to emit
            metric_from_config: Config key naming the metric this node consumes
            emits_metrics_from_config: Config key selecting the metrics this enricher emits
            metric_config_corequisites: Config keys that only take effect with a metric

        Returns:
            Decorator that registers the node
        """

        def decorator(func: NodeFn) -> NodeFn:
            # Derive category from ID if not provided
            derived_category = category
            if not derived_category and "." in node_id:
                prefix = node_id.split(".", 1)[0]
                if prefix in self.get_valid_categories():
                    derived_category = prefix

            # Enforce category type
            if derived_category not in self.get_valid_categories():
                raise ValueError(f"Invalid node category: {derived_category}")

            # Create metadata
            metadata: NodeMetadata = {
                "id": node_id,
                "description": description,
                "category": derived_category,
            }
            if input_type is not None:
                metadata["input_type"] = input_type
            if output_type is not None:
                metadata["output_type"] = output_type
            if required_connectors is not None:
                metadata["required_connectors"] = required_connectors
            if requires_enricher is not None:
                metadata["requires_enricher"] = requires_enricher
            if requires_metric is not None:
                metadata["requires_metric"] = requires_metric
            if metric_from_config is not None:
                metadata["metric_from_config"] = metric_from_config
            if emits_metrics_from_config is not None:
                metadata["emits_metrics_from_config"] = emits_metrics_from_config
            if metric_config_corequisites is not None:
                metadata["metric_config_corequisites"] = metric_config_corequisites

            # Store in registry directly — no wrapper overhead
            self._registry[node_id] = (func, metadata)
            return func

        return decorator

    def get_node(self, node_id: str) -> tuple[NodeFn, NodeMetadata]:
        """Get a node by ID.

        Args:
            node_id: The node's unique identifier

        Returns:
            Tuple of (node_function, metadata)

        Raises:
            KeyError: If node not found
        """
        if node_id not in self._registry:
            raise KeyError(f"Node not found: {node_id}")
        return self._registry[node_id]

    def list_nodes(self) -> dict[str, NodeMetadata]:
        """List all registered nodes."""
        return {cid: meta for cid, (_, meta) in self._registry.items()}

    @staticmethod
    def get_valid_categories() -> frozenset[NodeType]:
        """Get all valid node categories, derived from the ``NodeType`` alias."""
        return _VALID_CATEGORIES


# Create global registry instance
registry = NodeRegistry()

# Export main decorator for clean imports
node = registry.node

# Export utility functions with clear names
get_node = registry.get_node
list_nodes = registry.list_nodes
