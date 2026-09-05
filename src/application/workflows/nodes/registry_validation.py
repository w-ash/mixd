"""Registry integrity check, run once before the first workflow execution.

Guards the registries that must agree: every node registered by
``nodes/catalog.py`` needs a ``config_fields`` entry, or its config is never
validated and the editor renders it with no fields; and every enricher
dependency a node declares (``NodeMetadata``) must point at a registered
enricher, one of its metrics, and config keys that node actually declares —
otherwise the workflow validator would silently check nothing.
"""

from .config_fields import get_enricher_metric_names, get_node_config_fields
from .registry import NodeMetadata, NodeRegistry, registry


def _dependency_problems(
    node_id: str,
    meta: NodeMetadata,
    enrichers: set[str],
    declared_keys: set[str],
) -> list[str]:
    """Describe every invalid dependency declaration on one node."""
    problems: list[str] = []
    enricher = meta.get("requires_enricher")
    if enricher is not None and enricher not in enrichers:
        problems.append(f"{node_id}: requires_enricher '{enricher}' is not an enricher")
    if (metric := meta.get("requires_metric")) is not None:
        emitted = get_enricher_metric_names().get(enricher or "", frozenset[str]())
        if metric not in emitted:
            problems.append(
                f"{node_id}: requires_metric '{metric}' is not emitted by '{enricher}'"
            )
    config_keys = [
        meta.get("metric_from_config"),
        meta.get("emits_metrics_from_config"),
        *meta.get("metric_config_corequisites", {}),
    ]
    problems.extend(
        f"{node_id}: '{key}' is not a declared config field"
        for key in config_keys
        if key is not None and key not in declared_keys
    )
    return problems


def validate_registry(node_registry: NodeRegistry = registry) -> None:
    """Raise ``RuntimeError`` when the node registry is empty or drifts from config_fields.

    ``node_registry`` defaults to the process-wide registry; tests pass a
    throwaway instance to exercise the invariants without mutating it.
    """
    all_nodes = node_registry.list_nodes()

    if not all_nodes:
        raise RuntimeError("Node registry is empty — nodes/catalog.py was not imported")

    all_fields = get_node_config_fields()
    undeclared = sorted(all_nodes.keys() - all_fields.keys())
    if undeclared:
        raise RuntimeError(
            "Registered nodes missing a config_fields entry: " + ", ".join(undeclared)
        )

    enrichers = {
        nid for nid, meta in all_nodes.items() if meta["category"] == "enricher"
    }
    problems = [
        problem
        for node_id, meta in sorted(all_nodes.items())
        for problem in _dependency_problems(
            node_id, meta, enrichers, {f.key for f in all_fields[node_id]}
        )
    ]
    if problems:
        raise RuntimeError(
            "Registered nodes declare invalid enricher dependencies: "
            + "; ".join(problems)
        )
