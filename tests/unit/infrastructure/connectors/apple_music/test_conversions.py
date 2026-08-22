"""Tests for Apple Music → domain conversions.

Validates ``create_track_from_apple_song``: field mapping, ISRC
normalization, missing-optionals handling, and required-field refusal.

Fixtures are PROVISIONAL pending the live-probe packet.
"""

import pytest

from src.infrastructure.connectors.apple_music.conversions import (
    create_track_from_apple_song,
)
from tests.fixtures import make_apple_song


class TestCreateTrackFromAppleSong:
    def test_full_song_maps_all_fields(self):
        song = make_apple_song(
            song_id="1613600188",
            name="Test Song",
            artist="Test Artist",
            album="Test Album",
            duration_ms=200_000,
            isrc="USUM72309818",
        )

        track = create_track_from_apple_song("1613600188", song, user_id="u1")

        assert track.title == "Test Song"
        assert [a.name for a in track.artists] == ["Test Artist"]
        assert track.album == "Test Album"
        assert track.duration_ms == 200_000
        assert track.isrc == "USUM72309818"
        assert track.user_id == "u1"
        assert track.connector_track_identifiers["apple"] == "1613600188"

    def test_isrc_is_normalized(self):
        song = make_apple_song(isrc="us-um7-23-09818")
        track = create_track_from_apple_song(song.id, song, user_id="u1")
        assert track.isrc == "USUM72309818"

    def test_invalid_isrc_becomes_none(self):
        song = make_apple_song(isrc="not-an-isrc")
        track = create_track_from_apple_song(song.id, song, user_id="u1")
        assert track.isrc is None

    def test_missing_optionals_handled(self):
        song = make_apple_song(album="", duration_ms=0, isrc=None, release_date=None)
        track = create_track_from_apple_song(song.id, song, user_id="u1")
        assert track.album is None
        assert track.duration_ms is None
        assert track.isrc is None

    def test_missing_title_raises(self):
        song = make_apple_song()
        song.attributes.name = ""
        with pytest.raises(ValueError, match="title"):
            create_track_from_apple_song(song.id, song, user_id="u1")

    def test_missing_artist_raises(self):
        song = make_apple_song(artist="")
        with pytest.raises(ValueError, match="artist"):
            create_track_from_apple_song(song.id, song, user_id="u1")

    def test_track_keyed_on_given_id_not_song_id(self):
        """The caller passes the *current* id (catalogId on divergence)."""
        song = make_apple_song(song_id="old101", catalog_id="new202")
        track = create_track_from_apple_song("new202", song, user_id="u1")
        assert track.connector_track_identifiers["apple"] == "new202"
