"""Registry-declared connector links resolve by registry name or service alias."""

from src.infrastructure.connectors._shared.external_urls import (
    connector_playlist_url,
    connector_track_url,
)
from src.infrastructure.connectors.discovery import discover_connectors


class TestConnectorTrackUrl:
    def test_declared_hook_builds_the_connectors_own_page(self):
        assert (
            connector_track_url("spotify", "4cOdK2wGLETKBW3PvgPWqT")
            == "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"
        )

    def test_connector_without_a_hook_yields_none(self):
        # Last.fm stores "artist::title" composites, which address no page.
        assert connector_track_url("lastfm", "aphex twin::xtal") is None

    def test_unknown_service_yields_none(self):
        assert connector_track_url("myspace", "123") is None

    def test_lookup_uses_the_data_plane_service_alias(self):
        # Apple's rows key on "apple" while its config is registered under
        # "apple_music"; the alias is what a mapping row carries.
        config = discover_connectors()["apple_music"]
        assert config["play_service_name"] == "apple"
        assert connector_track_url("apple", "1440857781") is None


class TestConnectorPlaylistUrl:
    def test_declared_hook_builds_the_connectors_own_page(self):
        assert (
            connector_playlist_url("spotify", "37i9dQZF1DXcBWIGoYBM5M")
            == "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        )

    def test_connector_without_a_playlist_hook_yields_none(self):
        # MusicBrainz declares a track page but has no playlists.
        assert connector_playlist_url("musicbrainz", "abc") is None

    def test_unknown_service_yields_none(self):
        assert connector_playlist_url("myspace", "123") is None

    def test_apple_music_declares_no_playlist_page(self):
        # Library playlists are private to the signed-in member, so no public
        # page exists to link to under either name the connector answers to.
        assert connector_playlist_url("apple_music", "p.abc") is None
        assert connector_playlist_url("apple", "p.abc") is None
