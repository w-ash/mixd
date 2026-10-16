"""Unit tests for connector discovery's failure mode."""

import sys

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
    def test_import_error_propagates(
        self, uncached_registry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Every connector is first-party: a module that cannot import is a
        # bug. Swallowing it used to cache a reduced registry for the process
        # lifetime, which served required connector pickers empty.
        # A None entry in sys.modules makes the import system refuse the
        # module, so the failure arrives through the real import path.
        monkeypatch.setitem(sys.modules, "src.infrastructure.connectors.lastfm", None)

        with pytest.raises(ImportError, match="lastfm"):
            _ = discovery.discover_connectors()

        assert discovery._connectors_cache is None

    def test_live_registry_lists_every_first_party_connector(
        self, uncached_registry
    ) -> None:
        registry = discovery.discover_connectors()

        # listenbrainz is a lookup client with no connector config of its own.
        assert set(registry) == {
            "apple_music",
            "discogs",
            "lastfm",
            "musicbrainz",
            "spotify",
            "tidal",
        }
