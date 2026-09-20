"""Tests for Spotify track conversion — ``convert_spotify_track_to_connector``.

Pins the ``raw_metadata`` shape: the full model dump plus ``album_id``,
``explicit`` and the positional ``artist_ids`` that stay aligned with the
credits the connector track carries.
"""

from src.infrastructure.connectors.spotify.conversions import (
    convert_spotify_track_to_connector,
    spotify_artist_ids,
)
from src.infrastructure.connectors.spotify.models import SpotifyAlbum, SpotifyArtist
from tests.fixtures import make_spotify_track


class TestArtistIds:
    def test_artist_ids_are_positional_with_credits(self):
        track = make_spotify_track(
            "sp1",
            "Song",
            artists=[
                SpotifyArtist(id="a1", name="Alpha"),
                SpotifyArtist(id="a2", name="Beta"),
            ],
        )

        ct = convert_spotify_track_to_connector(track)

        assert [a.credited_name for a in ct.artists] == ["Alpha", "Beta"]
        assert ct.raw_metadata["artist_ids"] == ["a1", "a2"]

    def test_missing_id_is_none_and_keeps_alignment(self):
        track = make_spotify_track(
            "sp1",
            "Song",
            artists=[SpotifyArtist(name="NoId"), SpotifyArtist(id="a2", name="Beta")],
        )

        ct = convert_spotify_track_to_connector(track)

        assert [a.credited_name for a in ct.artists] == ["NoId", "Beta"]
        assert ct.raw_metadata["artist_ids"] == [None, "a2"]

    def test_every_artist_keeps_its_credit_and_id(self):
        track = make_spotify_track(
            "sp1",
            "Song",
            artists=[SpotifyArtist(id="ghost"), SpotifyArtist(id="a2", name="Beta")],
        )

        ct = convert_spotify_track_to_connector(track)

        assert [a.credited_name for a in ct.artists] == ["", "Beta"]
        assert ct.raw_metadata["artist_ids"] == ["ghost", "a2"]

    def test_spotify_artist_ids_maps_missing_id_to_none(self):
        artists = [SpotifyArtist(name="NoId"), SpotifyArtist(id="a2", name="Beta")]
        assert spotify_artist_ids(artists) == [None, "a2"]


class TestRawMetadataShape:
    def test_carries_full_dump_with_album_id_and_explicit(self):
        track = make_spotify_track(
            "sp1", "Song", album=SpotifyAlbum(id="alb1", name="Album"), explicit=True
        )

        ct = convert_spotify_track_to_connector(track)

        assert ct.raw_metadata["album_id"] == "alb1"
        assert ct.raw_metadata["explicit"] is True
        assert ct.raw_metadata["id"] == "sp1"
        assert ct.raw_metadata["name"] == "Song"

    def test_album_id_is_none_without_album(self):
        track = make_spotify_track("sp1", "Song", album=None)

        ct = convert_spotify_track_to_connector(track)

        assert ct.raw_metadata["album_id"] is None
