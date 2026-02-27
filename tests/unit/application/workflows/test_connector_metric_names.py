"""Characterization tests for _get_connector_metric_names in node_factories.

Locks down metric name resolution behavior before removing legacy alias blocks.
"""

from unittest.mock import patch

from src.application.workflows.node_factories import _get_connector_metric_names

# The function imports get_connector_metrics locally, so we patch at the source module.
_PATCH_TARGET = (
    "src.infrastructure.connectors._shared.metric_registry.get_connector_metrics"
)


class TestGetConnectorMetricNames:
    """Tests for _get_connector_metric_names resolution."""

    def test_exact_match(self):
        """Exact metric name match returns as-is."""
        with patch(
            _PATCH_TARGET,
            return_value=["lastfm_user_playcount", "lastfm_listeners"],
        ):
            result = _get_connector_metric_names("lastfm", ["lastfm_user_playcount"])
        assert result == ["lastfm_user_playcount"]

    def test_prefixed_match(self):
        """Connector-prefixed attribute resolves correctly."""
        with patch(
            _PATCH_TARGET,
            return_value=["spotify_popularity"],
        ):
            result = _get_connector_metric_names("spotify", ["popularity"])
        assert result == ["spotify_popularity"]

    def test_prefixed_lastfm_alias(self):
        """Prefixed matching: user_playcount → lastfm_user_playcount."""
        with patch(
            _PATCH_TARGET,
            return_value=[
                "lastfm_user_playcount",
                "lastfm_global_playcount",
                "lastfm_listeners",
            ],
        ):
            result = _get_connector_metric_names("lastfm", ["user_playcount"])
        assert result == ["lastfm_user_playcount"]

    def test_prefixed_spotify_alias(self):
        """Prefixed matching: popularity → spotify_popularity."""
        with patch(
            _PATCH_TARGET,
            return_value=["spotify_popularity"],
        ):
            result = _get_connector_metric_names("spotify", ["popularity"])
        assert result == ["spotify_popularity"]

    def test_unknown_attribute_returns_empty(self):
        """Unknown attribute logs warning and is not included."""
        with patch(
            _PATCH_TARGET,
            return_value=["lastfm_user_playcount"],
        ):
            result = _get_connector_metric_names("lastfm", ["nonexistent_attr"])
        assert result == []

    def test_unknown_connector_returns_empty(self):
        """Unknown connector returns empty list."""
        with patch(
            _PATCH_TARGET,
            return_value=[],
        ):
            result = _get_connector_metric_names("unknown_svc", ["anything"])
        assert result == []

    def test_multiple_attributes_resolved(self):
        """Multiple attributes are each resolved."""
        with patch(
            _PATCH_TARGET,
            return_value=[
                "lastfm_user_playcount",
                "lastfm_global_playcount",
                "lastfm_listeners",
            ],
        ):
            result = _get_connector_metric_names(
                "lastfm", ["lastfm_user_playcount", "lastfm_listeners"]
            )
        assert result == ["lastfm_user_playcount", "lastfm_listeners"]
