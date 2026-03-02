"""Registry validation for workflow node completeness.

Validates that all registered workflow nodes are resolvable via get_node()
at module load time, providing clear error messages when registration fails.
"""

from src.config import get_logger

from .node_registry import get_node, registry

logger = get_logger(__name__)


def validate_registry():
    """Validate registry integrity by verifying every registered node is resolvable.

    Instead of maintaining a hardcoded list that drifts, we verify that every
    node registered by node_catalog.py is actually findable via get_node().
    This makes it impossible for the validation list to fall out of sync.
    """
    all_nodes = registry.list_nodes()

    if not all_nodes:
        raise RuntimeError("Node registry is empty — node_catalog.py was not imported")

    broken: list[str] = []
    for node_id in all_nodes:
        try:
            get_node(node_id)
        except KeyError:
            broken.append(node_id)

    if broken:
        broken_str = ", ".join(broken)
        raise RuntimeError(f"Node registry broken: cannot resolve {broken_str}")

    return True, f"Node registry validated with {len(all_nodes)} nodes"
