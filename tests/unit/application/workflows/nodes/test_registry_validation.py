"""Tests for the registry integrity check.

Each invariant is exercised on a throwaway ``NodeRegistry`` so the
process-wide registry (populated by ``nodes/catalog.py``) is never mutated.
"""

from collections.abc import Mapping

import pytest

from src.application.workflows.nodes import catalog
from src.application.workflows.nodes.registry import NodeRegistry
from src.application.workflows.nodes.registry_validation import validate_registry
from src.application.workflows.protocols import NodeResult
from src.domain.entities.shared import JsonValue


async def _noop(
    _context: dict[str, object], _config: Mapping[str, JsonValue]
) -> NodeResult:
    return {}


def _registry_with(**declarations: dict[str, object]) -> NodeRegistry:
    """Build a throwaway registry from ``{node_id: node() kwargs}``."""
    node_registry = NodeRegistry()
    for node_id, kwargs in declarations.items():
        _ = node_registry.node(node_id, **kwargs)(_noop)
    return node_registry


class TestValidateRegistry:
    def test_production_catalog_passes(self):
        """The real catalog's declarations satisfy every invariant."""
        assert catalog
        validate_registry()

    def test_empty_registry_is_rejected(self):
        with pytest.raises(RuntimeError, match="registry is empty"):
            validate_registry(NodeRegistry())

    def test_node_without_config_fields_entry_is_rejected(self):
        node_registry = _registry_with(**{"filter.nonexistent": {}})
        with pytest.raises(RuntimeError, match="missing a config_fields entry"):
            validate_registry(node_registry)

    def test_requires_enricher_must_name_a_registered_enricher(self):
        node_registry = _registry_with(**{
            "filter.by_preference": {"requires_enricher": "enricher.preferences"},
        })
        with pytest.raises(
            RuntimeError, match=r"'enricher\.preferences' is not an enricher"
        ):
            validate_registry(node_registry)

    def test_requires_metric_must_be_emitted_by_that_enricher(self):
        node_registry = _registry_with(**{
            "enricher.play_history": {},
            "filter.by_first_played_date": {
                "requires_enricher": "enricher.play_history",
                "requires_metric": "no_such_metric",
            },
        })
        with pytest.raises(RuntimeError, match="'no_such_metric' is not emitted"):
            validate_registry(node_registry)

    def test_metric_from_config_must_be_a_declared_field(self):
        node_registry = _registry_with(**{
            "filter.by_metric": {"metric_from_config": "metric"},
        })
        with pytest.raises(
            RuntimeError, match="'metric' is not a declared config field"
        ):
            validate_registry(node_registry)

    def test_corequisite_keys_must_be_declared_fields(self):
        node_registry = _registry_with(**{
            "enricher.play_history": {
                "emits_metrics_from_config": "metrics",
                "metric_config_corequisites": {"window_days": "period_plays"},
            },
        })
        with pytest.raises(
            RuntimeError, match="'window_days' is not a declared config field"
        ):
            validate_registry(node_registry)

    def test_valid_dependency_declarations_pass(self):
        node_registry = _registry_with(**{
            "enricher.play_history": {
                "emits_metrics_from_config": "metrics",
                "metric_config_corequisites": {"period_days": "period_plays"},
            },
            "filter.by_first_played_date": {
                "requires_enricher": "enricher.play_history",
                "requires_metric": "first_played_dates",
            },
            "sorter.by_metric": {"metric_from_config": "metric_name"},
        })
        validate_registry(node_registry)
