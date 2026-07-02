"""Characterization + regression tests for Last.fm track→connector conversion.

Locks the observable contract of ``LastFMConnector.convert_track_to_connector``
(raw Last.fm track payload → ``ConnectorTrack``): the ``connector_track_identifier``
fallback chain (mbid → url → ``lastfm:{title}``), the seconds→ms duration
conversion, the dict-or-str artist/album handling, and the presence-gated metric
metadata. Written against the pre-refactor hand-walk implementation and left
unchanged after the boundary refactor, proving identical output entities for
identical raw payloads.

The connector's ``convert_track_to_connector`` is the stable seam: it accepts a
raw ``Mapping`` before and after the refactor, while the private
``convert_lastfm_track_to_connector`` moves from raw-Mapping to typed
``LastFMTrackData``. Testing the seam exercises validation + conversion end to end.
"""

from collections.abc import Callable, Mapping
from unittest.mock import patch

import pytest

from src.domain.entities import Artist, ConnectorTrack
from src.domain.entities.shared import JsonValue
from src.infrastructure.connectors.lastfm.connector import LastFMConnector


@pytest.fixture
def convert() -> Callable[[Mapping[str, JsonValue]], ConnectorTrack]:
    """Bound ``convert_track_to_connector`` from a client-less connector.

    ``__attrs_post_init__`` (which builds the HTTP client) is patched out;
    ``convert_track_to_connector`` is pure and never touches ``_client``.
    """
    with patch.object(LastFMConnector, "__attrs_post_init__"):
        connector = LastFMConnector()
    return connector.convert_track_to_connector


class TestArtistExtraction:
    def test_dict_artist(
        self, convert: Callable[[Mapping[str, JsonValue]], ConnectorTrack]
    ) -> None:
        ct = convert({"name": "Song", "artist": {"name": "Radiohead"}})
        assert ct.artists == [Artist(name="Radiohead")]

    def test_str_artist(
        self, convert: Callable[[Mapping[str, JsonValue]], ConnectorTrack]
    ) -> None:
        ct = convert({"name": "Song", "artist": "Radiohead"})
        assert ct.artists == [Artist(name="Radiohead")]

    def test_dict_artist_without_name_yields_no_artist(
        self, convert: Callable[[Mapping[str, JsonValue]], ConnectorTrack]
    ) -> None:
        ct = convert({"name": "Song", "artist": {"foo": "bar"}, "url": "http://u"})
        assert ct.artists == []

    def test_missing_artist_yields_no_artist(
        self, convert: Callable[[Mapping[str, JsonValue]], ConnectorTrack]
    ) -> None:
        ct = convert({"name": "Song", "url": "http://u"})
        assert ct.artists == []

    def test_empty_str_artist_yields_no_artist(
        self, convert: Callable[[Mapping[str, JsonValue]], ConnectorTrack]
    ) -> None:
        ct = convert({"name": "Song", "artist": "", "url": "http://u"})
        assert ct.artists == []


class TestAlbumExtraction:
    def test_dict_album(
        self, convert: Callable[[Mapping[str, JsonValue]], ConnectorTrack]
    ) -> None:
        ct = convert({"name": "Song", "album": {"name": "Pablo Honey"}})
        assert ct.album == "Pablo Honey"

    def test_str_album(
        self, convert: Callable[[Mapping[str, JsonValue]], ConnectorTrack]
    ) -> None:
        ct = convert({"name": "Song", "album": "Pablo Honey"})
        assert ct.album == "Pablo Honey"

    def test_missing_album(
        self, convert: Callable[[Mapping[str, JsonValue]], ConnectorTrack]
    ) -> None:
        ct = convert({"name": "Song", "artist": "A", "url": "http://u"})
        assert ct.album is None

    def test_empty_str_album_preserved(
        self, convert: Callable[[Mapping[str, JsonValue]], ConnectorTrack]
    ) -> None:
        # The str branch keeps "" verbatim (no truthiness gate) — locked.
        ct = convert({"name": "Song", "album": ""})
        assert ct.album == ""

    def test_dict_album_without_name(
        self, convert: Callable[[Mapping[str, JsonValue]], ConnectorTrack]
    ) -> None:
        # dict branch reads the literal "name" key only; "#text" is ignored.
        ct = convert({"name": "Song", "album": {"#text": "Ignored"}})
        assert ct.album is None


class TestDurationConversion:
    def test_string_duration_seconds_to_ms(
        self, convert: Callable[[Mapping[str, JsonValue]], ConnectorTrack]
    ) -> None:
        ct = convert({"name": "Song", "duration": "238"})
        assert ct.duration_ms == 238000

    def test_int_duration_seconds_to_ms(
        self, convert: Callable[[Mapping[str, JsonValue]], ConnectorTrack]
    ) -> None:
        ct = convert({"name": "Song", "duration": 238})
        assert ct.duration_ms == 238000

    def test_non_digit_duration_ignored(
        self, convert: Callable[[Mapping[str, JsonValue]], ConnectorTrack]
    ) -> None:
        ct = convert({"name": "Song", "duration": "3:58"})
        assert ct.duration_ms is None

    def test_missing_duration(
        self, convert: Callable[[Mapping[str, JsonValue]], ConnectorTrack]
    ) -> None:
        ct = convert({"name": "Song"})
        assert ct.duration_ms is None


class TestConnectorIdentifierFallbackChain:
    def test_mbid_wins(
        self, convert: Callable[[Mapping[str, JsonValue]], ConnectorTrack]
    ) -> None:
        ct = convert({
            "name": "Song",
            "mbid": "the-mbid",
            "url": "http://u",
        })
        assert ct.connector_track_identifier == "the-mbid"

    def test_url_fallback_when_no_mbid(
        self, convert: Callable[[Mapping[str, JsonValue]], ConnectorTrack]
    ) -> None:
        ct = convert({"name": "Song", "url": "http://u"})
        assert ct.connector_track_identifier == "http://u"

    def test_name_fallback_when_no_mbid_or_url(
        self, convert: Callable[[Mapping[str, JsonValue]], ConnectorTrack]
    ) -> None:
        ct = convert({"name": "Song"})
        assert ct.connector_track_identifier == "lastfm:Song"

    def test_empty_mbid_falls_through_to_url(
        self, convert: Callable[[Mapping[str, JsonValue]], ConnectorTrack]
    ) -> None:
        ct = convert({"name": "Song", "mbid": "", "url": "http://u"})
        assert ct.connector_track_identifier == "http://u"


class TestRawMetadata:
    def test_metrics_present_are_coerced(
        self, convert: Callable[[Mapping[str, JsonValue]], ConnectorTrack]
    ) -> None:
        ct = convert({
            "name": "Song",
            "playcount": "1000",
            "listeners": "500",
            "userplaycount": "3",
            "mbid": "the-mbid",
        })
        assert ct.raw_metadata == {
            "lastfm_global_playcount": 1000,
            "lastfm_listeners": 500,
            "lastfm_user_playcount": 3,
            "lastfm_mbid": "the-mbid",
        }

    def test_metrics_absent_yields_empty_metadata(
        self, convert: Callable[[Mapping[str, JsonValue]], ConnectorTrack]
    ) -> None:
        ct = convert({"name": "Song", "url": "http://u"})
        assert ct.raw_metadata == {}

    def test_zero_metric_present_is_kept(
        self, convert: Callable[[Mapping[str, JsonValue]], ConnectorTrack]
    ) -> None:
        # Presence-gated: a provided-but-zero playcount stays in metadata as 0,
        # distinct from an absent key.
        ct = convert({"name": "Song", "playcount": 0})
        assert ct.raw_metadata == {"lastfm_global_playcount": 0}

    def test_mbid_only_added_when_truthy(
        self, convert: Callable[[Mapping[str, JsonValue]], ConnectorTrack]
    ) -> None:
        ct = convert({"name": "Song", "mbid": "", "url": "http://u"})
        assert ct.raw_metadata == {}


class TestFullPayload:
    def test_full_dict_payload(
        self, convert: Callable[[Mapping[str, JsonValue]], ConnectorTrack]
    ) -> None:
        ct = convert({
            "name": "Creep",
            "artist": {"name": "Radiohead"},
            "album": {"name": "Pablo Honey"},
            "duration": "238",
            "playcount": "1000",
            "listeners": "500",
            "userplaycount": "3",
            "mbid": "creep-mbid",
            "url": "https://www.last.fm/music/Radiohead/_/Creep",
        })
        assert ct.connector_name == "lastfm"
        assert ct.connector_track_identifier == "creep-mbid"
        assert ct.title == "Creep"
        assert ct.artists == [Artist(name="Radiohead")]
        assert ct.album == "Pablo Honey"
        assert ct.duration_ms == 238000
        assert ct.isrc is None
        assert ct.release_date is None
        assert ct.raw_metadata == {
            "lastfm_global_playcount": 1000,
            "lastfm_listeners": 500,
            "lastfm_user_playcount": 3,
            "lastfm_mbid": "creep-mbid",
        }
