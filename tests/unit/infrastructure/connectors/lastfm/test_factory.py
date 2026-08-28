"""Unit tests for Last.fm factory wiring.

``create_play_resolver`` resolves its cross-discovery dependency through the
connector registry's ``cross_discovery_factory`` declaration instead of a
concrete Spotify import.
"""

from unittest.mock import patch

from src.infrastructure.connectors.lastfm.factory import create_play_resolver

_DISCOVERY = "src.infrastructure.connectors.discovery.discover_connectors"


class TestCreatePlayResolver:
    def test_uses_the_declared_cross_discovery_factory(self) -> None:
        sentinel = object()
        fake_config = {"cross_discovery_factory": lambda: sentinel}

        with patch(_DISCOVERY, return_value={"faux": fake_config}):
            resolver = create_play_resolver()

        assert resolver._inward_resolver._cross_discovery is sentinel

    def test_no_declared_provider_degrades_to_none(self) -> None:
        with patch(_DISCOVERY, return_value={}):
            resolver = create_play_resolver()

        assert resolver._inward_resolver._cross_discovery is None

    def test_production_registry_supplies_the_spotify_provider(self) -> None:
        from src.infrastructure.connectors.spotify.cross_discovery import (
            SpotifyCrossDiscoveryProvider,
        )

        resolver = create_play_resolver()

        assert isinstance(
            resolver._inward_resolver._cross_discovery, SpotifyCrossDiscoveryProvider
        )
