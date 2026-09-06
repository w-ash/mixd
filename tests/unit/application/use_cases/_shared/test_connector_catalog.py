"""The connector catalog serves registry metadata to the application layer."""

import pytest

from src.application.use_cases._shared.connector_catalog import (
    default_connector_catalog,
)


class TestDefaultConnectorCatalog:
    """``default_connector_catalog`` bridges to the concrete registry."""

    def test_descriptors_cover_every_registered_connector(self) -> None:
        catalog = default_connector_catalog()

        names = {descriptor.name for descriptor in catalog.list_descriptors()}

        assert "spotify" in names
        assert catalog.describe("spotify").display_name == "Spotify"

    def test_describe_rejects_an_unregistered_connector(self) -> None:
        with pytest.raises(ValueError, match="Unknown connector: myspace"):
            _ = default_connector_catalog().describe("myspace")


class TestPlaylistUrl:
    """Playlist links come from the registry, never from a guessed pattern."""

    def test_declared_hook_returns_the_connectors_own_page(self) -> None:
        assert (
            default_connector_catalog().playlist_url(
                "spotify", "3cEYpjA9oz9GiPac4AsH4n"
            )
            == "https://open.spotify.com/playlist/3cEYpjA9oz9GiPac4AsH4n"
        )

    def test_connector_without_a_playlist_page_returns_none(self) -> None:
        assert default_connector_catalog().playlist_url("apple_music", "p.abc") is None

    def test_unregistered_connector_returns_none(self) -> None:
        assert default_connector_catalog().playlist_url("myspace", "1") is None
