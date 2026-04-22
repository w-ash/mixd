"""Static validation of every production workflow template.

Walks `src/application/workflows/definitions/*.json` (excluding `dev/`) and
asserts each file parses into a `WorkflowDef` whose `name` is non-empty
(caught one template shipping as "Unknown" due to a `workflow_name` typo),
whose tasks all reference registered node types, whose upstream refs resolve,
and whose task graph is acyclic.

This file is the regression guard for template authoring — importing a
template via JSON bypasses Python type checking, so this is the place to
catch stale node names, dangling upstream IDs, and the "Unknown" default.
"""

from pathlib import Path

import pytest

# Registers every node type as a side effect; get_node() below depends on it.
import src.application.workflows.node_catalog as _node_catalog
from src.application.workflows.node_registry import get_node
from src.application.workflows.validation import compute_parallel_levels
from src.application.workflows.workflow_loader import load_workflow_def

_NODE_CATALOG_MODULE = _node_catalog.__name__

_DEFINITIONS_DIR = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "application"
    / "workflows"
    / "definitions"
)

# Production templates only — the dev/ subdirectory holds fixtures used by
# other tests and isn't guaranteed to satisfy these invariants.
_PRODUCTION_TEMPLATES: list[Path] = sorted(_DEFINITIONS_DIR.glob("*.json"))


@pytest.mark.parametrize(
    "template_path",
    _PRODUCTION_TEMPLATES,
    ids=[p.stem for p in _PRODUCTION_TEMPLATES],
)
class TestProductionTemplate:
    """One parametrized instance per production template JSON file."""

    def test_loads_with_non_empty_name(self, template_path: Path) -> None:
        """Every template must set `name` — the loader's "Unknown" default is a bug."""
        wf = load_workflow_def(template_path)
        assert wf.name, f"{template_path.name}: missing or empty 'name' field"
        assert wf.name != "Unknown", (
            f"{template_path.name}: name fell back to 'Unknown' — did you use "
            f"'workflow_name' instead of 'name'?"
        )

    def test_has_tasks(self, template_path: Path) -> None:
        wf = load_workflow_def(template_path)
        assert wf.tasks, f"{template_path.name}: template has no tasks"

    def test_all_task_types_are_registered(self, template_path: Path) -> None:
        """Every task.type must resolve in the node registry."""
        wf = load_workflow_def(template_path)
        for task in wf.tasks:
            try:
                get_node(task.type)
            except KeyError:
                pytest.fail(
                    f"{template_path.name}: task '{task.id}' references "
                    f"unregistered node type '{task.type}'"
                )

    def test_upstream_refs_resolve(self, template_path: Path) -> None:
        """Every upstream ID must point to another task in the same workflow."""
        wf = load_workflow_def(template_path)
        task_ids = {t.id for t in wf.tasks}
        for task in wf.tasks:
            for upstream_id in task.upstream:
                assert upstream_id in task_ids, (
                    f"{template_path.name}: task '{task.id}' references "
                    f"unknown upstream '{upstream_id}'"
                )

    def test_task_graph_is_acyclic(self, template_path: Path) -> None:
        """compute_parallel_levels raises on cycles — just call it."""
        wf = load_workflow_def(template_path)
        compute_parallel_levels(wf.tasks)
