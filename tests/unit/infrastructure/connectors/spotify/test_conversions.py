"""Tests for Spotify track conversion — ``convert_spotify_track_to_connector``.

Pins the credits: each carries the Spotify artist id on itself (``None``
where Spotify sent none), and the ``raw_metadata`` shape stays the full model
dump plus ``album_id`` and ``explicit``.
"""

from src.infrastructure.connectors.spotify.conversions import (
    convert_spotify_track_to_connector,
    spotify_artist_credits,
)
from src.infrastructure.connectors.spotify.models import SpotifyAlbum, SpotifyArtist
from tests.fixtures import make_spotify_track


def _credits(ct) -> list[tuple[str, str | None]]:
    return [(a.credited_name, a.connector_artist_identifier) for a in ct.artists]


class TestArtistCredits:
    def test_each_credit_carries_its_artist_id(self):
        track = make_spotify_track(
            "sp1",
            "Song",
            artists=[
                SpotifyArtist(id="a1", name="Alpha"),
                SpotifyArtist(id="a2", name="Beta"),
            ],
        )

        ct = convert_spotify_track_to_connector(track)

        assert _credits(ct) == [("Alpha", "a1"), ("Beta", "a2")]

    def test_missing_id_is_none_on_that_credit(self):
        track = make_spotify_track(
            "sp1",
            "Song",
            artists=[SpotifyArtist(name="NoId"), SpotifyArtist(id="a2", name="Beta")],
        )

        ct = convert_spotify_track_to_connector(track)

        assert _credits(ct) == [("NoId", None), ("Beta", "a2")]

    def test_every_artist_keeps_its_credit_and_id(self):
        track = make_spotify_track(
            "sp1",
            "Song",
            artists=[SpotifyArtist(id="ghost"), SpotifyArtist(id="a2", name="Beta")],
        )

        ct = convert_spotify_track_to_connector(track)

        assert _credits(ct) == [("", "ghost"), ("Beta", "a2")]

    def test_no_artist_ids_ride_in_the_raw_metadata(self):
        ct = convert_spotify_track_to_connector(make_spotify_track("sp1", "Song"))
        assert "artist_ids" not in ct.raw_metadata

    def test_spotify_artist_credits_maps_missing_id_to_none(self):
        artists = [SpotifyArtist(name="NoId"), SpotifyArtist(id="a2", name="Beta")]
        assert [
            (c.credited_name, c.connector_artist_identifier)
            for c in spotify_artist_credits(artists)
        ] == [("NoId", None), ("Beta", "a2")]


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
