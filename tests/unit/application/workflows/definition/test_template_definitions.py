"""Static validation of every production workflow template.

Walks `src/application/workflows/definitions/*.json` (excluding `dev/`) and
asserts each file parses into a `WorkflowDef` whose `name` is non-empty
(caught one template shipping as "Unknown" due to a `workflow_name` typo) and
that passes the real validator with no errors or warnings.

This file is the regression guard for template authoring — importing a
template via JSON bypasses Python type checking, so this is the place to
catch stale node names, dangling upstream IDs, and the "Unknown" default.
"""

from pathlib import Path

import pytest

from src.application.workflows.definition.loader import load_workflow_def
from src.application.workflows.definition.validation import (
    validate_workflow_def_detailed,
)

# Registers every node type as a side effect; the validator depends on it.
import src.application.workflows.nodes.catalog as _catalog

_CATALOG_MODULE = _catalog.__name__

_DEFINITIONS_DIR = (
    Path(__file__).resolve().parents[5]
    / "src"
    / "application"
    / "workflows"
    / "definitions"
)

# Production templates only — the dev/ subdirectory holds fixtures used by
# other tests and isn't guaranteed to satisfy these invariants.
_PRODUCTION_TEMPLATES: list[Path] = sorted(_DEFINITIONS_DIR.glob("*.json"))

# Every shipped definition, dev and personal seeds included.
_ALL_DEFINITIONS: list[Path] = sorted(_DEFINITIONS_DIR.rglob("*.json"))


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


@pytest.mark.parametrize(
    "definition_path",
    _ALL_DEFINITIONS,
    ids=[str(p.relative_to(_DEFINITIONS_DIR)) for p in _ALL_DEFINITIONS],
)
def test_every_seed_validates_clean(definition_path: Path) -> None:
    """Every shipped definition passes the save/execute validator with zero items.

    Zero items means: tasks present, every type registered, every upstream
    resolves, the graph is acyclic, config is complete and in range, and no
    consumer lacks its upstream enricher. A warning would show as a badge the
    moment a user opened the template in the editor.

    dev/discovery_mix.json is the one seed exercising ``exclusion_source`` as a
    task_ref, so this is where a regression in the task_ref check would show.
    """
    wf = load_workflow_def(definition_path)
    assert validate_workflow_def_detailed(wf) == []
