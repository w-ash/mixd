"""Integration test: end-to-end freshness propagation from settings → connectors → registry.

Validates that importing a connector module populates the metric registry
with freshness values derived from FreshnessConfig in settings.
"""

import pytest

from src.config.settings import settings
from src.infrastructure.connectors._shared.metric_registry import (
    _connector_metrics,
    _field_mappings,
    _metric_freshness,
    get_metric_freshness,
)


@pytest.fixture(autouse=True)
def _clean_registries():
    """Snapshot and restore global registries between tests.

    Connector modules run register_metrics() at import time, so the registries
    may already be populated. We snapshot, let the test run, then restore.
    """
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


class TestLastFMFreshnessRegistration:
    """After importing lastfm connector, freshness matches settings."""

    def test_lastfm_user_playcount_freshness(self):
        # Force re-import to trigger registration (already imported at module level)
        from src.infrastructure.connectors.lastfm.connector import (
            LastFmMetricResolver,  # noqa: F401
        )

        assert (
            get_metric_freshness("lastfm_user_playcount")
            == settings.freshness.lastfm_hours
        )

    def test_lastfm_global_playcount_freshness(self):
        from src.infrastructure.connectors.lastfm.connector import (
            LastFmMetricResolver,  # noqa: F401
        )

        assert (
            get_metric_freshness("lastfm_global_playcount")
            == settings.freshness.lastfm_hours
        )

    def test_lastfm_listeners_freshness(self):
        from src.infrastructure.connectors.lastfm.connector import (
            LastFmMetricResolver,  # noqa: F401
        )

        assert (
            get_metric_freshness("lastfm_listeners") == settings.freshness.lastfm_hours
        )


class TestSpotifyFreshnessRegistration:
    """After importing spotify connector, freshness matches settings."""

    def test_explicit_flag_freshness(self):
        from src.infrastructure.connectors.spotify.connector import (
            SpotifyMetricResolver,  # noqa: F401
        )

        assert get_metric_freshness("explicit_flag") == settings.freshness.spotify_hours
