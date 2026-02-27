"""Tests for register_metrics() in base.py with freshness propagation."""

from unittest.mock import MagicMock

import pytest

from src.infrastructure.connectors._shared.metric_registry import (
    DEFAULT_METRIC_FRESHNESS,
    _connector_metrics,
    _field_mappings,
    _metric_freshness,
    get_metric_freshness,
)
from src.infrastructure.connectors.base import register_metrics


@pytest.fixture(autouse=True)
def _clean_registries():
    """Snapshot and restore global registries between tests."""
    saved = (
        dict(_connector_metrics),
        dict(_field_mappings),
        dict(_metric_freshness),
    )
    yield
    _connector_metrics.clear()
    _connector_metrics.update(saved[0])
    _field_mappings.clear()
    _field_mappings.update(saved[1])
    _metric_freshness.clear()
    _metric_freshness.update(saved[2])


class TestRegisterMetricsWithFreshness:
    """Tests for register_metrics() freshness_map parameter."""

    def _make_resolver(self, connector: str) -> MagicMock:
        resolver = MagicMock()
        resolver.CONNECTOR = connector
        return resolver

    def test_freshness_map_populates_registry(self):
        """register_metrics with freshness_map populates get_metric_freshness()."""
        resolver = self._make_resolver("test_svc")
        field_map = {"metric_a": "field_a", "metric_b": "field_b"}
        freshness_map = {"metric_a": 2.0, "metric_b": 48.0}

        register_metrics(resolver, field_map, freshness_map)

        assert get_metric_freshness("metric_a") == 2.0
        assert get_metric_freshness("metric_b") == 48.0

    def test_no_freshness_map_leaves_defaults(self):
        """register_metrics without freshness_map leaves default freshness."""
        resolver = self._make_resolver("test_svc")
        field_map = {"metric_c": "field_c"}

        register_metrics(resolver, field_map)

        assert get_metric_freshness("metric_c") == DEFAULT_METRIC_FRESHNESS

    def test_each_metric_gets_own_freshness(self):
        """Each metric in the field_map gets its own freshness from the map."""
        resolver = self._make_resolver("test_svc")
        field_map = {"fast_metric": "field_fast", "slow_metric": "field_slow"}
        freshness_map = {"fast_metric": 1.0, "slow_metric": 168.0}

        register_metrics(resolver, field_map, freshness_map)

        assert get_metric_freshness("fast_metric") == 1.0
        assert get_metric_freshness("slow_metric") == 168.0

    def test_partial_freshness_map(self):
        """Metrics not in freshness_map get default freshness."""
        resolver = self._make_resolver("test_svc")
        field_map = {"with_fresh": "field_a", "without_fresh": "field_b"}
        freshness_map = {"with_fresh": 6.0}

        register_metrics(resolver, field_map, freshness_map)

        assert get_metric_freshness("with_fresh") == 6.0
        assert get_metric_freshness("without_fresh") == DEFAULT_METRIC_FRESHNESS

    def test_field_mappings_still_registered(self):
        """Field mappings work correctly alongside freshness_map."""
        from src.infrastructure.connectors._shared.metric_registry import (
            get_field_name,
        )

        resolver = self._make_resolver("test_svc")
        field_map = {"metric_x": "api_field_x"}
        freshness_map = {"metric_x": 12.0}

        register_metrics(resolver, field_map, freshness_map)

        assert get_field_name("metric_x") == "api_field_x"
        assert get_metric_freshness("metric_x") == 12.0
