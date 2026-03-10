"""Unit tests for Spotify URL/URI/raw ID parser."""

import pytest

from src.application.use_cases._shared.playlist_id_parser import (
    parse_playlist_identifier,
)


class TestParseSpotifyPlaylistId:
    """Spotify playlist identifier parsing."""

    def test_raw_id(self):
        assert (
            parse_playlist_identifier("spotify", "37i9dQZF1DZ06evO05tE88")
            == "37i9dQZF1DZ06evO05tE88"
        )

    def test_spotify_uri(self):
        result = parse_playlist_identifier(
            "spotify", "spotify:playlist:37i9dQZF1DZ06evO05tE88"
        )
        assert result == "37i9dQZF1DZ06evO05tE88"

    def test_spotify_url(self):
        result = parse_playlist_identifier(
            "spotify", "https://open.spotify.com/playlist/37i9dQZF1DZ06evO05tE88"
        )
        assert result == "37i9dQZF1DZ06evO05tE88"

    def test_spotify_url_with_query_params(self):
        result = parse_playlist_identifier(
            "spotify",
            "https://open.spotify.com/playlist/37i9dQZF1DZ06evO05tE88?si=abc123",
        )
        assert result == "37i9dQZF1DZ06evO05tE88"

    def test_spotify_url_http(self):
        result = parse_playlist_identifier(
            "spotify", "http://open.spotify.com/playlist/37i9dQZF1DZ06evO05tE88"
        )
        assert result == "37i9dQZF1DZ06evO05tE88"

    def test_invalid_spotify_id_raises(self):
        with pytest.raises(ValueError, match="Cannot parse Spotify"):
            parse_playlist_identifier("spotify", "not-a-valid-id!")

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            parse_playlist_identifier("spotify", "")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            parse_playlist_identifier("spotify", "   ")


class TestParseOtherConnectors:
    """Non-Spotify connectors pass through raw ID."""

    def test_apple_music_passthrough(self):
        assert parse_playlist_identifier("apple_music", "pl.u-abc123") == "pl.u-abc123"

    def test_strips_whitespace(self):
        assert parse_playlist_identifier("lastfm", "  my-id  ") == "my-id"
