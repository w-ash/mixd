"""Tests for Tidal → domain conversions.

Validates ``create_track_from_tidal_detail``: field mapping (duration in
seconds → ms), ISRC normalization, missing-optionals handling, and
required-field refusal (title, artists). Artists arrive on the detail, not
the track value — Tidal serves them as side-loaded relationship resources.
"""

import pytest

from src.infrastructure.connectors.tidal.conversions import (
    create_track_from_tidal_detail,
    normalized_tidal_isrc,
    tidal_duration_ms,
)
from src.infrastructure.connectors.tidal.models import TidalTrack, TidalTrackDetail


def _detail(
    track_id: str = "12345",
    title: str = "Test Song",
    isrc: str | None = "USUM72309818",
    duration_seconds: int | None = 200,
    artist_names: tuple[str, ...] = ("Test Artist",),
    replacement_id: str | None = None,
) -> TidalTrackDetail:
    return TidalTrackDetail(
        track=TidalTrack(
            id=track_id, title=title, isrc=isrc, duration_seconds=duration_seconds
        ),
        artist_names=artist_names,
        replacement_id=replacement_id,
    )


class TestCreateTrackFromTidalDetail:
    def test_full_detail_maps_all_fields(self):
        detail = _detail()

        track = create_track_from_tidal_detail("12345", detail, user_id="u1")

        assert track.title == "Test Song"
        assert [a.name for a in track.artists] == ["Test Artist"]
        assert track.duration_ms == 200_000
        assert track.isrc == "USUM72309818"
        assert track.user_id == "u1"
        assert track.connector_track_identifiers["tidal"] == "12345"

    def test_multiple_artists_preserve_order(self):
        detail = _detail(artist_names=("Main Artist", "Featured Artist"))
        track = create_track_from_tidal_detail("12345", detail, user_id="u1")
        assert [a.name for a in track.artists] == ["Main Artist", "Featured Artist"]

    def test_isrc_is_normalized(self):
        detail = _detail(isrc="us-um7-23-09818")
        track = create_track_from_tidal_detail("12345", detail, user_id="u1")
        assert track.isrc == "USUM72309818"

    def test_invalid_isrc_becomes_none(self):
        detail = _detail(isrc="not-an-isrc")
        track = create_track_from_tidal_detail("12345", detail, user_id="u1")
        assert track.isrc is None

    def test_missing_optionals_handled(self):
        detail = _detail(isrc=None, duration_seconds=None)
        track = create_track_from_tidal_detail("12345", detail, user_id="u1")
        assert track.isrc is None
        assert track.duration_ms is None
        assert track.album is None

    def test_missing_title_raises(self):
        detail = _detail(title="")
        with pytest.raises(ValueError, match="title"):
            create_track_from_tidal_detail("12345", detail, user_id="u1")

    def test_missing_artists_raises(self):
        detail = _detail(artist_names=())
        with pytest.raises(ValueError, match="artist"):
            create_track_from_tidal_detail("12345", detail, user_id="u1")

    def test_track_keyed_on_given_id_not_resource_id(self):
        """The caller passes the *current* id (the successor on substitution)."""
        detail = _detail(track_id="old101")
        track = create_track_from_tidal_detail("new202", detail, user_id="u1")
        assert track.connector_track_identifiers["tidal"] == "new202"


class TestHelpers:
    def test_duration_ms_from_seconds(self):
        assert tidal_duration_ms(_detail().track) == 200_000

    def test_duration_ms_none_when_unknown(self):
        assert tidal_duration_ms(_detail(duration_seconds=None).track) is None

    def test_normalized_isrc_none_when_absent(self):
        assert normalized_tidal_isrc(_detail(isrc=None).track) is None
