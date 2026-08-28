"""Tests for the Spotify factory's cross-discovery wiring."""

from unittest.mock import AsyncMock, patch

from src.infrastructure.connectors.listenbrainz.lookup import ListenBrainzLookup
from src.infrastructure.connectors.spotify.cross_discovery import (
    SpotifyCrossDiscoveryProvider,
)
from src.infrastructure.connectors.spotify.factory import (
    create_cross_discovery_provider,
)


class TestCreateCrossDiscoveryProvider:
    async def test_arms_the_listenbrainz_lookup(self):
        """The factory constructs the provider with a live ListenBrainz
        lookup — the pre-resolution arm is enabled, not left None."""
        with patch(
            "src.infrastructure.connectors.spotify.connector.SpotifyConnector",
            return_value=AsyncMock(),
        ):
            provider = create_cross_discovery_provider()
        try:
            assert isinstance(provider._listenbrainz_lookup, ListenBrainzLookup)
        finally:
            await provider.aclose()

    async def test_factory_built_connector_is_closed_on_aclose(self):
        """The factory keeps no reference to the connector it builds, so the
        provider owns it and aclose() must close its pool."""
        connector = AsyncMock()
        with patch(
            "src.infrastructure.connectors.spotify.connector.SpotifyConnector",
            return_value=connector,
        ):
            provider = create_cross_discovery_provider()
        await provider.aclose()
        connector.aclose.assert_awaited_once()


class TestAcloseOwnership:
    async def test_injected_connector_stays_open(self):
        connector = AsyncMock()
        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        await provider.aclose()
        connector.aclose.assert_not_awaited()
