"""Tests for Spotify utility functions.

Validates create_track_from_spotify_data correctly converts SpotifyTrack Pydantic
models into domain Track entities with proper field mapping and validation.
"""

import pytest

from src.domain.entities import Artist
from src.infrastructure.connectors.spotify.models import (
    SpotifyAlbum,
    SpotifyArtist,
    SpotifyExternalIds,
    SpotifyTrack,
)
from src.infrastructure.connectors.spotify.utilities import (
    create_track_from_spotify_data,
)


class TestCreateTrackFromSpotifyData:
    """Happy path: valid SpotifyTrack produces correct domain Track."""

    def test_basic_track_conversion(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Test Song",
            artists=[SpotifyArtist(name="Test Artist")],
            album=SpotifyAlbum(name="Test Album"),
            duration_ms=240000,
            external_ids=SpotifyExternalIds(isrc="USRC12345678"),
        )

        track = create_track_from_spotify_data("abc123", spotify_track)

        assert track.title == "Test Song"
        assert track.artists == [Artist(name="Test Artist")]
        assert track.album == "Test Album"
        assert track.duration_ms == 240000
        assert track.isrc == "USRC12345678"
        assert track.connector_track_identifiers.get("spotify") == "abc123"

    def test_multiple_artists(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Collab Song",
            artists=[
                SpotifyArtist(name="Artist A"),
                SpotifyArtist(name="Artist B"),
            ],
        )

        track = create_track_from_spotify_data("abc123", spotify_track)

        assert len(track.artists) == 2
        assert track.artists[0].name == "Artist A"
        assert track.artists[1].name == "Artist B"

    def test_no_album(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Single",
            artists=[SpotifyArtist(name="Artist")],
            album=None,
        )

        track = create_track_from_spotify_data("abc123", spotify_track)

        assert track.album is None

    def test_zero_duration_treated_as_none(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Song",
            artists=[SpotifyArtist(name="Artist")],
            duration_ms=0,
        )

        track = create_track_from_spotify_data("abc123", spotify_track)

        assert track.duration_ms is None

    def test_no_isrc(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Song",
            artists=[SpotifyArtist(name="Artist")],
        )

        track = create_track_from_spotify_data("abc123", spotify_track)

        assert track.isrc is None


class TestCreateTrackFromSpotifyDataValidation:
    """Error cases: missing or invalid data raises ValueError."""

    def test_empty_name_raises(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="",
            artists=[SpotifyArtist(name="Artist")],
        )

        with pytest.raises(ValueError, match="Missing track title"):
            create_track_from_spotify_data("abc123", spotify_track)

    def test_no_artists_raises(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Song",
            artists=[],
        )

        with pytest.raises(ValueError, match="Missing artists"):
            create_track_from_spotify_data("abc123", spotify_track)

    def test_artists_with_empty_names_raises(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Song",
            artists=[SpotifyArtist(name=""), SpotifyArtist(name="")],
        )

        with pytest.raises(ValueError, match="No valid artist names"):
            create_track_from_spotify_data("abc123", spotify_track)

    def test_skips_empty_artist_names(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Song",
            artists=[SpotifyArtist(name=""), SpotifyArtist(name="Valid")],
        )

        track = create_track_from_spotify_data("abc123", spotify_track)

        assert len(track.artists) == 1
        assert track.artists[0].name == "Valid"
