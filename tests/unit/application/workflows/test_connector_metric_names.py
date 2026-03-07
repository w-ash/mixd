"""Tests for _get_connector_metric_names in node_factories.

Validates metric name resolution: exact match, connector-prefixed match,
unknown attributes, and unknown connectors.
"""

from unittest.mock import MagicMock

from src.application.workflows.node_factories import _get_connector_metric_names


def _make_metric_config(metrics: list[str]) -> MagicMock:
    """Create a mock MetricConfigProvider returning the given metrics."""
    mock = MagicMock()
    mock.get_connector_metrics.return_value = metrics
    return mock


class TestGetConnectorMetricNames:
    """Tests for _get_connector_metric_names resolution."""

    def test_exact_match(self):
        """Exact metric name match returns as-is."""
        mc = _make_metric_config(["lastfm_user_playcount", "lastfm_listeners"])
        result = _get_connector_metric_names(mc, "lastfm", ["lastfm_user_playcount"])
        assert result == ["lastfm_user_playcount"]

    def test_prefixed_match(self):
        """Connector-prefixed attribute resolves correctly."""
        mc = _make_metric_config(["explicit_flag"])
        result = _get_connector_metric_names(mc, "spotify", ["explicit_flag"])
        assert result == ["explicit_flag"]

    def test_prefixed_lastfm_alias(self):
        """Prefixed matching: user_playcount -> lastfm_user_playcount."""
        mc = _make_metric_config([
            "lastfm_user_playcount",
            "lastfm_global_playcount",
            "lastfm_listeners",
        ])
        result = _get_connector_metric_names(mc, "lastfm", ["user_playcount"])
        assert result == ["lastfm_user_playcount"]

    def test_prefixed_spotify_alias(self):
        """Exact matching: explicit_flag resolves directly."""
        mc = _make_metric_config(["explicit_flag"])
        result = _get_connector_metric_names(mc, "spotify", ["explicit_flag"])
        assert result == ["explicit_flag"]

    def test_unknown_attribute_returns_empty(self):
        """Unknown attribute logs warning and is not included."""
        mc = _make_metric_config(["lastfm_user_playcount"])
        result = _get_connector_metric_names(mc, "lastfm", ["nonexistent_attr"])
        assert result == []

    def test_unknown_connector_returns_empty(self):
        """Unknown connector returns empty list."""
        mc = _make_metric_config([])
        result = _get_connector_metric_names(mc, "unknown_svc", ["anything"])
        assert result == []

    def test_multiple_attributes_resolved(self):
        """Multiple attributes are each resolved."""
        mc = _make_metric_config([
            "lastfm_user_playcount",
            "lastfm_global_playcount",
            "lastfm_listeners",
        ])
        result = _get_connector_metric_names(
            mc, "lastfm", ["lastfm_user_playcount", "lastfm_listeners"]
        )
        assert result == ["lastfm_user_playcount", "lastfm_listeners"]
