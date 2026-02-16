"""
Node registry system for workflow orchestration.

This module provides a centralized registry with a clean, declarative API for
node registration and discovery. It serves as the connection point between
workflow definitions and node implementations.
"""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Literal, NotRequired, TypedDict

# Type definitions with modern annotation style
type NodeType = Literal[
    "source",
    "enricher",
    "filter",
    "sorter",
    "selector",
    "combiner",
    "destination",
]

# Define strict node function type
type NodeFn = Callable[[dict, dict], Awaitable[dict]]


class NodeMetadata(TypedDict):
    """Type-safe node metadata."""

    id: str
    description: str
    category: NodeType
    input_type: NotRequired[str]
    output_type: NotRequired[str]
    factory_created: NotRequired[bool]


# Singleton registry using a class-based pattern
class NodeRegistry:
    """Registry for workflow nodes with simplified discovery."""

    def __init__(self) -> None:
        self._registry: dict[str, tuple[NodeFn, NodeMetadata]] = {}

    def register(
        self,
        node_id: str,
        *,
        description: str = "",
        input_type: str | None = None,
        output_type: str | None = None,
        category: NodeType | None = None,
    ) -> Callable[[NodeFn], NodeFn]:
        """Register a node with the registry.

        Args:
            node_id: Unique identifier (e.g., "source.playlist")
            description: Human-readable description
            input_type: Type of input the node expects
            output_type: Type of output the node produces
            category: Node category (source, filter, etc.)

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
                "category": derived_category,  # type: ignore - we validated above
            }
            if input_type is not None:
                metadata["input_type"] = input_type
            if output_type is not None:
                metadata["output_type"] = output_type
            if hasattr(func, "__factory__"):
                metadata["factory_created"] = True

            # Preserve function metadata with wraps
            @wraps(func)
            async def wrapper(context: dict, config: dict) -> dict:
                return await func(context, config)

            # Store in registry
            self._registry[node_id] = (wrapper, metadata)
            return wrapper

        return decorator

    def node(self, node_id: str, **kwargs) -> Callable[[NodeFn], NodeFn]:
        """Simpler alias for register."""
        return self.register(node_id, **kwargs)

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

    def get_by_category(self, category: NodeType) -> dict[str, NodeMetadata]:
        """Get nodes filtered by category."""
        return {
            cid: meta
            for cid, (_, meta) in self._registry.items()
            if meta["category"] == category
        }

    @staticmethod
    def get_valid_categories() -> set[NodeType]:
        """Get all valid node categories."""
        return {
            "source",
            "enricher",
            "filter",
            "sorter",
            "selector",
            "combiner",
            "destination",
        }


# Create global registry instance
registry = NodeRegistry()

# Export main decorator for clean imports
node = registry.node

# Export utility functions with clear names
get_node = registry.get_node
list_nodes = registry.list_nodes
get_nodes_by_category = registry.get_by_category
