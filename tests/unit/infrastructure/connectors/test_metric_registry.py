"""Characterization tests for the dynamic connector metric registry.

Locks down registration, lookup, and configuration behavior before renaming.
"""

from unittest.mock import MagicMock

import pytest

from src.infrastructure.connectors._shared.metric_registry import (
    DEFAULT_METRIC_FRESHNESS,
    _connector_metrics,
    _field_mappings,
    _metric_freshness,
    get_connector_metrics,
    get_field_name,
    get_metric_freshness,
    register_metric_resolver,
)


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


class TestRegisterMetricResolver:
    """Tests for register_metric_resolver."""

    def test_populates_connector_metrics(self):
        resolver = MagicMock()
        resolver.CONNECTOR = "test_svc"
        register_metric_resolver("test_metric", resolver)
        assert "test_metric" in _connector_metrics["test_svc"]

    def test_no_duplicate_in_connector_metrics(self):
        resolver = MagicMock()
        resolver.CONNECTOR = "test_svc"
        register_metric_resolver("test_metric", resolver)
        register_metric_resolver("test_metric", resolver)
        assert _connector_metrics["test_svc"].count("test_metric") == 1


class TestGetConnectorMetrics:
    """Tests for get_connector_metrics."""

    def test_returns_registered_metrics(self):
        resolver = MagicMock()
        resolver.CONNECTOR = "my_svc"
        register_metric_resolver("my_metric_a", resolver)
        register_metric_resolver("my_metric_b", resolver)
        result = get_connector_metrics("my_svc")
        assert set(result) == {"my_metric_a", "my_metric_b"}

    def test_returns_empty_for_unknown_connector(self):
        assert get_connector_metrics("nonexistent_connector") == []


class TestGetFieldName:
    """Tests for get_field_name."""

    def test_returns_registered_field_mapping(self):
        _field_mappings["test_metric"] = "api_field"
        assert get_field_name("test_metric") == "api_field"

    def test_returns_metric_name_as_fallback(self):
        assert get_field_name("unregistered_metric") == "unregistered_metric"


class TestGetMetricFreshness:
    """Tests for get_metric_freshness."""

    def test_returns_registered_freshness(self):
        _metric_freshness["test_metric"] = 48.0
        assert get_metric_freshness("test_metric") == 48.0

    def test_returns_default_for_unregistered(self):
        assert get_metric_freshness("unregistered_metric") == DEFAULT_METRIC_FRESHNESS


class TestFreshnessRegistration:
    """Tests for freshness propagation through register_metric_config."""

    def test_register_metric_config_with_freshness(self):
        """register_metric_config with freshness_hours stores it in registry."""
        from src.infrastructure.connectors._shared.metric_registry import (
            register_metric_config,
        )

        register_metric_config("fresh_metric", "field_a", freshness_hours=6.0)
        assert get_metric_freshness("fresh_metric") == 6.0

    def test_register_metric_config_without_freshness_leaves_default(self):
        """register_metric_config without freshness_hours leaves default."""
        from src.infrastructure.connectors._shared.metric_registry import (
            register_metric_config,
        )

        register_metric_config("no_fresh_metric", "field_b")
        assert get_metric_freshness("no_fresh_metric") == DEFAULT_METRIC_FRESHNESS

    def test_later_registration_overwrites_freshness(self):
        """Later registration overwrites earlier freshness value."""
        from src.infrastructure.connectors._shared.metric_registry import (
            register_metric_config,
        )

        register_metric_config("overwrite_metric", "field_c", freshness_hours=12.0)
        assert get_metric_freshness("overwrite_metric") == 12.0

        register_metric_config("overwrite_metric", "field_c", freshness_hours=48.0)
        assert get_metric_freshness("overwrite_metric") == 48.0
