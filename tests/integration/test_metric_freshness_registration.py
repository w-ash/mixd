"""Integration test: settings → connector config → discovery → metric registry.

Validates that connector discovery registers each connector's declared
metrics with field mappings and freshness values derived from
FreshnessConfig in settings.
"""

import pytest

from src.config.settings import settings
from src.infrastructure.connectors import discovery
from src.infrastructure.connectors._shared.metric_registry import (
    _connector_metrics,
    _field_mappings,
    _metric_freshness,
    get_connector_metrics,
    get_field_name,
    get_metric_freshness,
)


@pytest.fixture(autouse=True)
def _fresh_discovery(monkeypatch: pytest.MonkeyPatch):
    """Force a fresh discovery run and restore all global state afterwards.

    Discovery caches its registry process-wide and registers metrics only on
    a fresh run, so the cache is reset before each test and the metric
    registries are snapshotted and restored.
    """
    saved = (
        dict(_connector_metrics),
        dict(_field_mappings),
        dict(_metric_freshness),
    )
    monkeypatch.setattr(discovery, "_connectors_cache", None)
    yield
    _connector_metrics.clear()
    _connector_metrics.update(saved[0])
    _field_mappings.clear()
    _field_mappings.update(saved[1])
    _metric_freshness.clear()
    _metric_freshness.update(saved[2])


class TestDiscoveryRegistersLastFmMetrics:
    """After discovery, Last.fm metrics and freshness match declarations."""

    def test_metrics_registered_for_connector(self):
        discovery.discover_connectors()

        assert set(get_connector_metrics("lastfm")) >= {
            "lastfm_user_playcount",
            "lastfm_global_playcount",
            "lastfm_listeners",
        }

    def test_freshness_matches_settings(self):
        discovery.discover_connectors()

        for metric in (
            "lastfm_user_playcount",
            "lastfm_global_playcount",
            "lastfm_listeners",
        ):
            assert get_metric_freshness(metric) == settings.freshness.lastfm_hours


class TestDiscoveryRegistersSpotifyMetrics:
    """After discovery, Spotify metrics and freshness match declarations."""

    def test_explicit_flag_registered(self):
        discovery.discover_connectors()

        assert "explicit_flag" in get_connector_metrics("spotify")
        assert get_field_name("explicit_flag") == "explicit"

    def test_explicit_flag_freshness(self):
        discovery.discover_connectors()

        assert get_metric_freshness("explicit_flag") == settings.freshness.spotify_hours
