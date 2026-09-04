"""Tests for Spotify playlist identifier parsing.

Covers the URL / URI / bare-ID forms accepted by
``parse_spotify_playlist_id`` and the connector method that exposes it,
plus the empty and unparseable rejections.
"""

import pytest

from src.infrastructure.connectors.spotify.connector import SpotifyConnector
from src.infrastructure.connectors.spotify.playlist_identifiers import (
    parse_spotify_playlist_id,
)

_PLAYLIST_ID = "37i9dQZF1DZ06evO05tE88"


class TestParseSpotifyPlaylistId:
    """Accepted identifier forms."""

    def test_bare_id_passes_through(self):
        assert parse_spotify_playlist_id(_PLAYLIST_ID) == _PLAYLIST_ID

    def test_url(self):
        raw = f"https://open.spotify.com/playlist/{_PLAYLIST_ID}"
        assert parse_spotify_playlist_id(raw) == _PLAYLIST_ID

    def test_url_with_query_params(self):
        raw = f"https://open.spotify.com/playlist/{_PLAYLIST_ID}?si=abc123"
        assert parse_spotify_playlist_id(raw) == _PLAYLIST_ID

    def test_uri(self):
        assert parse_spotify_playlist_id(f"spotify:playlist:{_PLAYLIST_ID}") == (
            _PLAYLIST_ID
        )

    def test_surrounding_whitespace_is_trimmed(self):
        assert parse_spotify_playlist_id(f"  {_PLAYLIST_ID}  ") == _PLAYLIST_ID


class TestParseSpotifyPlaylistIdErrors:
    """Rejected identifier forms."""

    def test_empty_input(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            parse_spotify_playlist_id("")

    def test_whitespace_only_input(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            parse_spotify_playlist_id("   ")

    def test_unparseable_input(self):
        with pytest.raises(ValueError, match="Cannot parse Spotify playlist"):
            parse_spotify_playlist_id("not-a-valid-id!")


class TestConnectorParsePlaylistIdentifier:
    """The connector exposes the Spotify parser through the port method."""

    def test_connector_parses_url(self):
        connector = SpotifyConnector()
        raw = f"https://open.spotify.com/playlist/{_PLAYLIST_ID}"
        assert connector.parse_playlist_identifier(raw) == _PLAYLIST_ID

    def test_connector_rejects_empty(self):
        connector = SpotifyConnector()
        with pytest.raises(ValueError, match="cannot be empty"):
            connector.parse_playlist_identifier("  ")
