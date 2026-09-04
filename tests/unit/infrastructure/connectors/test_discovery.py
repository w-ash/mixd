"""Unit tests for connector discovery's failure mode."""

from unittest.mock import patch

import pytest

from src.infrastructure.connectors import discovery


@pytest.fixture
def uncached_registry():
    """Run discovery against an empty cache and restore the old one after."""
    saved = discovery._connectors_cache
    discovery._connectors_cache = None
    try:
        yield
    finally:
        discovery._connectors_cache = saved


class TestDiscoverConnectors:
    def test_import_error_propagates(self, uncached_registry) -> None:
        # Every connector is first-party: a module that cannot import is a
        # bug. Swallowing it used to cache a reduced registry for the process
        # lifetime, which served required connector pickers empty.
        with (
            patch.object(
                discovery,
                "_load_connector_config",
                side_effect=ImportError("No module named 'missing_dep'"),
            ),
            pytest.raises(ImportError, match="missing_dep"),
        ):
            _ = discovery.discover_connectors()

        assert discovery._connectors_cache is None

    def test_live_registry_lists_every_first_party_connector(
        self, uncached_registry
    ) -> None:
        registry = discovery.discover_connectors()

        assert {"spotify", "lastfm", "musicbrainz"} <= set(registry)
