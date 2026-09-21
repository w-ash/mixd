"""Tests for MusicBrainz data conversion utilities.

Covers the stateless transforms that turn raw MusicBrainz Web Services API
responses into domain ``ConnectorTrack`` entities: recording coercion,
metadata extraction, artist/release selection, and ISRC normalization.
"""

import pytest

from src.domain.entities import ConnectorTrack
from src.infrastructure.connectors.musicbrainz.conversions import (
    _ensure_recording,
    convert_musicbrainz_track_to_connector,
    extract_recording_metadata,
)
from src.infrastructure.connectors.musicbrainz.models import (
    MusicBrainzArtist,
    MusicBrainzArtistCredit,
    MusicBrainzRecording,
    MusicBrainzRelease,
)

OTHER_ARTIST_MBID = "c9f8e7d6-5b4a-4c3d-9e2f-1a0b9c8d7e6f"

RECORDING_MBID = "f5a1c4b7-9e3a-4a1e-8f7e-1d3c5a9b2c4d"
ARTIST_MBID = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
RELEASE_MBID = "b1c2d3e4-5678-9012-3456-789abcdef012"


def _make_recording(**overrides) -> MusicBrainzRecording:
    """Build a MusicBrainzRecording with full data; override any field."""
    defaults = {
        "id": RECORDING_MBID,
        "title": "Paranoid Android",
        "length": 390000,
        "artist_credit": [
            MusicBrainzArtistCredit(
                name="Radiohead",
                artist=MusicBrainzArtist(id=ARTIST_MBID, name="Radiohead"),
            )
        ],
        "releases": [MusicBrainzRelease(id=RELEASE_MBID, title="OK Computer")],
        "isrcs": ["GBAYE9600666"],
    }
    defaults.update(overrides)
    return MusicBrainzRecording(**defaults)


class TestEnsureRecording:
    """_ensure_recording passes typed models through and validates raw dicts."""

    def test_typed_model_passes_through(self):
        recording = _make_recording()
        assert _ensure_recording(recording) is recording

    def test_raw_dict_is_validated(self):
        raw = {
            "id": RECORDING_MBID,
            "title": "Creep",
            "artist-credit": [{"name": "Radiohead"}],
        }

        result = _ensure_recording(raw)

        assert isinstance(result, MusicBrainzRecording)
        assert result.id == RECORDING_MBID
        assert result.title == "Creep"
        assert result.artist_credit[0].name == "Radiohead"

    def test_missing_required_field_raises(self):
        with pytest.raises(Exception):
            _ensure_recording({"title": "Has no id"})


class TestExtractRecordingMetadata:
    """Happy paths for metadata extraction from recording data."""

    def test_full_recording_extracts_all_fields(self):
        recording = _make_recording()

        meta = extract_recording_metadata(recording)

        assert meta["musicbrainz_mbid"] == RECORDING_MBID
        assert meta["musicbrainz_title"] == "Paranoid Android"
        assert meta["musicbrainz_duration_ms"] == 390000
        assert meta["musicbrainz_artist_mbid"] == ARTIST_MBID
        assert meta["musicbrainz_artist_name"] == "Radiohead"
        assert meta["musicbrainz_release_mbid"] == RELEASE_MBID
        assert meta["musicbrainz_release_title"] == "OK Computer"
        assert meta["musicbrainz_isrc"] == "GBAYE9600666"

    def test_omits_length_when_none(self):
        recording = _make_recording(length=None)

        meta = extract_recording_metadata(recording)

        assert "musicbrainz_duration_ms" not in meta

    def test_omits_release_when_no_releases(self):
        recording = _make_recording(releases=[])

        meta = extract_recording_metadata(recording)

        assert "musicbrainz_release_mbid" not in meta
        assert "musicbrainz_release_title" not in meta

    def test_omits_isrc_when_no_isrcs(self):
        recording = _make_recording(isrcs=[])

        meta = extract_recording_metadata(recording)

        assert "musicbrainz_isrc" not in meta

    def test_omits_artist_name_when_empty(self):
        recording = _make_recording(
            artist_credit=[
                MusicBrainzArtistCredit(
                    name="",
                    artist=MusicBrainzArtist(id=ARTIST_MBID, name=""),
                )
            ]
        )

        meta = extract_recording_metadata(recording)

        assert meta["musicbrainz_artist_mbid"] == ARTIST_MBID
        assert "musicbrainz_artist_name" not in meta

    def test_accepts_raw_dict(self):
        raw = {
            "id": RECORDING_MBID,
            "title": "Creep",
            "artist-credit": [
                {
                    "name": "Radiohead",
                    "artist": {"id": ARTIST_MBID, "name": "Radiohead"},
                }
            ],
        }

        meta = extract_recording_metadata(raw)

        assert meta["musicbrainz_mbid"] == RECORDING_MBID
        assert meta["musicbrainz_artist_mbid"] == ARTIST_MBID

    def test_empty_input_returns_empty_dict(self):
        assert extract_recording_metadata({}) == {}

    def test_invalid_dict_returns_empty_dict(self):
        # Missing required `id` triggers ValidationError → swallowed → {}.
        assert extract_recording_metadata({"title": "no id"}) == {}


class TestConvertMusicBrainzTrackToConnectorHappyPath:
    """convert_musicbrainz_track_to_connector produces correct ConnectorTrack."""

    def test_basic_conversion(self):
        recording = _make_recording()

        track = convert_musicbrainz_track_to_connector(recording)

        assert isinstance(track, ConnectorTrack)
        assert track.connector_name == "musicbrainz"
        assert track.connector_track_identifier == RECORDING_MBID
        assert track.title == "Paranoid Android"
        assert [a.credited_name for a in track.artists] == ["Radiohead"]
        assert track.album == "OK Computer"
        assert track.duration_ms == 390000
        assert track.isrc == "GBAYE9600666"
        assert track.release_date is None
        assert track.raw_metadata["musicbrainz_mbid"] == RECORDING_MBID

    def test_multiple_artists(self):
        recording = _make_recording(
            artist_credit=[
                MusicBrainzArtistCredit(
                    name="Thom Yorke",
                    artist=MusicBrainzArtist(id=ARTIST_MBID, name="Thom Yorke"),
                ),
                MusicBrainzArtistCredit(
                    name="PJ Harvey",
                    artist=MusicBrainzArtist(id=ARTIST_MBID, name="PJ Harvey"),
                ),
            ]
        )

        track = convert_musicbrainz_track_to_connector(recording)

        assert [a.credited_name for a in track.artists] == ["Thom Yorke", "PJ Harvey"]

    def test_uses_only_first_release_for_album(self):
        recording = _make_recording(
            releases=[
                MusicBrainzRelease(id=RELEASE_MBID, title="OK Computer"),
                MusicBrainzRelease(id="other", title="Later Compilation"),
            ]
        )

        track = convert_musicbrainz_track_to_connector(recording)

        assert track.album == "OK Computer"

    def test_falls_back_to_credit_name_when_artist_absent(self):
        recording = _make_recording(
            artist_credit=[MusicBrainzArtistCredit(name="Credited Name", artist=None)]
        )

        track = convert_musicbrainz_track_to_connector(recording)

        assert [a.credited_name for a in track.artists] == ["Credited Name"]

    def test_skips_credits_with_no_usable_name(self):
        recording = _make_recording(
            artist_credit=[
                MusicBrainzArtistCredit(name="", artist=None),
                MusicBrainzArtistCredit(name="Valid", artist=None),
            ]
        )

        track = convert_musicbrainz_track_to_connector(recording)

        assert [a.credited_name for a in track.artists] == ["Valid"]


class TestArtistCreditsAndIds:
    """Join phrases land on the credit; MBIDs land positionally in metadata."""

    def test_join_phrase_is_carried_and_empty_reads_as_none(self):
        recording = _make_recording(
            artist_credit=[
                MusicBrainzArtistCredit(
                    name="Thom Yorke",
                    joinphrase=" & ",
                    artist=MusicBrainzArtist(id=ARTIST_MBID, name="Thom Yorke"),
                ),
                MusicBrainzArtistCredit(
                    name="PJ Harvey",
                    artist=MusicBrainzArtist(id=OTHER_ARTIST_MBID, name="PJ Harvey"),
                ),
            ]
        )

        track = convert_musicbrainz_track_to_connector(recording)

        assert [(a.credited_name, a.join_phrase) for a in track.artists] == [
            ("Thom Yorke", " & "),
            ("PJ Harvey", None),
        ]
        assert track.artists_display == "Thom Yorke & PJ Harvey"

    def test_each_credit_carries_its_artist_mbid(self):
        recording = _make_recording(
            artist_credit=[
                MusicBrainzArtistCredit(
                    name="Thom Yorke",
                    artist=MusicBrainzArtist(id=ARTIST_MBID, name="Thom Yorke"),
                ),
                MusicBrainzArtistCredit(name="Credited Only", artist=None),
                MusicBrainzArtistCredit(name="", artist=None),
                MusicBrainzArtistCredit(
                    name="PJ Harvey",
                    artist=MusicBrainzArtist(id=OTHER_ARTIST_MBID, name="PJ Harvey"),
                ),
            ]
        )

        track = convert_musicbrainz_track_to_connector(recording)

        assert [
            (a.credited_name, a.connector_artist_identifier) for a in track.artists
        ] == [
            ("Thom Yorke", ARTIST_MBID),
            ("Credited Only", None),
            ("PJ Harvey", OTHER_ARTIST_MBID),
        ]
        assert "artist_ids" not in track.raw_metadata

    def test_raw_json_joinphrase_and_artist_extras_parse(self):
        credit = MusicBrainzArtistCredit.model_validate({
            "name": "Caribou",
            "joinphrase": "",
            "artist": {
                "id": ARTIST_MBID,
                "name": "Caribou",
                "type": "Person",
                "disambiguation": "Dan Snaith",
                "aliases": [
                    {
                        "name": "Manitoba",
                        "sort-name": "Manitoba",
                        "type": "Artist name",
                        "locale": None,
                        "primary": None,
                    }
                ],
                "relations": [
                    {"type": "discogs", "url": {"resource": "https://discogs/1"}},
                    {"type": "wikidata"},
                ],
            },
        })

        assert credit.joinphrase == ""
        assert credit.artist is not None
        assert credit.artist.type == "Person"
        assert credit.artist.disambiguation == "Dan Snaith"
        assert credit.artist.aliases[0].name == "Manitoba"
        assert credit.artist.aliases[0].sort_name == "Manitoba"
        assert credit.artist.relations[0].url is not None
        assert credit.artist.relations[0].url.resource == "https://discogs/1"
        assert credit.artist.relations[1].url is None

    def test_artist_extras_default_empty(self):
        artist = MusicBrainzArtist(id=ARTIST_MBID)
        assert artist.type is None
        assert artist.disambiguation is None
        assert artist.aliases == []
        assert artist.relations == []


class TestConvertMusicBrainzTrackToConnectorEdgeCases:
    """Conversion behavior when optional fields are absent."""

    def test_no_release_produces_none_album(self):
        recording = _make_recording(releases=[])

        track = convert_musicbrainz_track_to_connector(recording)

        assert track.album is None

    def test_empty_release_title_produces_none_album(self):
        recording = _make_recording(
            releases=[MusicBrainzRelease(id=RELEASE_MBID, title="")]
        )

        track = convert_musicbrainz_track_to_connector(recording)

        assert track.album is None

    def test_no_isrcs_produces_none_isrc(self):
        recording = _make_recording(isrcs=[])

        track = convert_musicbrainz_track_to_connector(recording)

        assert track.isrc is None

    def test_invalid_isrc_normalized_to_none(self):
        recording = _make_recording(isrcs=["not-an-isrc"])

        track = convert_musicbrainz_track_to_connector(recording)

        assert track.isrc is None

    def test_hyphenated_isrc_is_normalized(self):
        recording = _make_recording(isrcs=["gb-aye-96-00666"])

        track = convert_musicbrainz_track_to_connector(recording)

        assert track.isrc == "GBAYE9600666"

    def test_none_length_produces_none_duration(self):
        recording = _make_recording(length=None)

        track = convert_musicbrainz_track_to_connector(recording)

        assert track.duration_ms is None

    def test_accepts_raw_dict(self):
        raw = {
            "id": RECORDING_MBID,
            "title": "Creep",
            "artist-credit": [{"name": "Radiohead"}],
        }

        track = convert_musicbrainz_track_to_connector(raw)

        assert track.connector_track_identifier == RECORDING_MBID
        assert track.title == "Creep"
        assert [a.credited_name for a in track.artists] == ["Radiohead"]


class TestConvertMusicBrainzTrackToConnectorValidation:
    """convert_musicbrainz_track_to_connector rejects unusable input."""

    def test_empty_mapping_raises(self):
        with pytest.raises(ValueError, match="recording data is required"):
            convert_musicbrainz_track_to_connector({})

    def test_missing_mbid_raises(self):
        # Pydantic requires `id` — providing an empty string bypasses validation
        # but must then be rejected by convert_musicbrainz_track_to_connector.
        recording = MusicBrainzRecording(id="", title="Orphan")

        with pytest.raises(ValueError, match="must have an ID"):
            convert_musicbrainz_track_to_connector(recording)
