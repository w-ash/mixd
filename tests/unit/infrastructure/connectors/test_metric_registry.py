"""Tests for the dynamic connector metric registry.

Covers register_metrics() (connector index, field mappings, freshness,
idempotency) and the lookup functions.
"""

import pytest

from src.infrastructure.connectors._shared.metric_registry import (
    DEFAULT_METRIC_FRESHNESS,
    _connector_metrics,
    _field_mappings,
    _metric_freshness,
    get_connector_metrics,
    get_field_name,
    get_metric_freshness,
    register_metrics,
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


class TestRegisterMetrics:
    """Tests for register_metrics."""

    def test_populates_connector_metrics(self):
        register_metrics("test_svc", {"test_metric": "test_field"})
        assert "test_metric" in _connector_metrics["test_svc"]

    def test_registers_field_mappings(self):
        register_metrics("test_svc", {"metric_x": "api_field_x"})
        assert get_field_name("metric_x") == "api_field_x"

    def test_registers_freshness_for_every_metric(self):
        register_metrics(
            "test_svc", {"metric_a": "field_a", "metric_b": "field_b"}, 2.0
        )
        assert get_metric_freshness("metric_a") == 2.0
        assert get_metric_freshness("metric_b") == 2.0

    def test_no_freshness_leaves_default(self):
        register_metrics("test_svc", {"metric_c": "field_c"})
        assert get_metric_freshness("metric_c") == DEFAULT_METRIC_FRESHNESS

    def test_reregistration_is_idempotent(self):
        register_metrics("test_svc", {"test_metric": "test_field"}, 6.0)
        register_metrics("test_svc", {"test_metric": "test_field"}, 6.0)
        assert _connector_metrics["test_svc"].count("test_metric") == 1
        assert get_field_name("test_metric") == "test_field"
        assert get_metric_freshness("test_metric") == 6.0

    def test_later_registration_overwrites_freshness(self):
        register_metrics("test_svc", {"overwrite_metric": "field_c"}, 12.0)
        assert get_metric_freshness("overwrite_metric") == 12.0

        register_metrics("test_svc", {"overwrite_metric": "field_c"}, 48.0)
        assert get_metric_freshness("overwrite_metric") == 48.0

    def test_empty_field_map_registers_nothing(self):
        register_metrics("metric_less_svc", {})
        assert "metric_less_svc" not in _connector_metrics


class TestGetConnectorMetrics:
    """Tests for get_connector_metrics."""

    def test_returns_registered_metrics(self):
        register_metrics("my_svc", {"my_metric_a": "field_a", "my_metric_b": "field_b"})
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
