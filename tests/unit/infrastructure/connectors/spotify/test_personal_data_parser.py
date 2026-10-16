"""Unit tests for Spotify personal data parser.

Tests the JSON parsing pipeline: raw Spotify export → SpotifyPlayRecord objects.
Covers happy path, malformed records, null-safety for optional fields, and edge cases.
"""

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from src.infrastructure.connectors.spotify.personal_data import (
    SpotifyPlayRecord,
    parse_spotify_personal_data,
)


def _make_valid_record(**overrides: object) -> dict:
    """Create a valid Spotify export JSON record with optional field overrides."""
    base = {
        "ts": "2024-06-15T14:30:00Z",
        "spotify_track_uri": "spotify:track:4iV5W9uYEdYUVa79Axb7Rh",
        "master_metadata_track_name": "Test Song",
        "master_metadata_album_artist_name": "Test Artist",
        "master_metadata_album_album_name": "Test Album",
        "ms_played": 240000,
        "platform": "Linux",
        "conn_country": "US",
        "reason_start": "trackdone",
        "reason_end": "trackdone",
        "shuffle": False,
        "skipped": False,
        "offline": False,
        "incognito_mode": False,
    }
    base.update(overrides)
    return base


class TestSpotifyPlayRecordFromJson:
    """Test SpotifyPlayRecord.from_json() parsing."""

    def test_valid_record_parses_correctly(self):
        record = SpotifyPlayRecord.from_json(_make_valid_record())

        assert record.timestamp == datetime(2024, 6, 15, 14, 30, tzinfo=UTC)
        assert record.track_name == "Test Song"
        assert record.artist_name == "Test Artist"
        assert record.album_name == "Test Album"
        assert record.track_uri == "spotify:track:4iV5W9uYEdYUVa79Axb7Rh"
        assert record.ms_played == 240000
        assert record.platform == "Linux"
        assert record.country == "US"
        assert record.reason_start == "trackdone"
        assert record.reason_end == "trackdone"
        assert record.shuffle is False
        assert record.skipped is False
        assert record.offline is False
        assert record.incognito_mode is False

    @pytest.mark.parametrize(
        "core_field",
        [
            "ts",
            "spotify_track_uri",
            "master_metadata_track_name",
            "master_metadata_album_artist_name",
            "master_metadata_album_album_name",
            "ms_played",
        ],
    )
    def test_missing_core_field_raises_key_error(self, core_field: str):
        data = _make_valid_record()
        del data[core_field]
        with pytest.raises(KeyError, match=core_field):
            SpotifyPlayRecord.from_json(data)

    @pytest.mark.parametrize(
        ("export_key", "attribute"),
        [
            ("platform", "platform"),
            ("conn_country", "country"),
            ("reason_start", "reason_start"),
            ("reason_end", "reason_end"),
        ],
    )
    def test_missing_behavioral_text_field_defaults_to_unknown(
        self, export_key: str, attribute: str
    ):
        data = _make_valid_record()
        del data[export_key]

        record = SpotifyPlayRecord.from_json(data)

        assert getattr(record, attribute) == "unknown"

    @pytest.mark.parametrize(
        "flag", ["shuffle", "skipped", "offline", "incognito_mode"]
    )
    @pytest.mark.parametrize("shape", ["null", "absent"])
    def test_null_or_absent_flag_defaults_to_false(self, flag: str, shape: str):
        """Spotify exports can carry null for these flags, or omit them."""
        data = _make_valid_record()
        if shape == "null":
            data[flag] = None
        else:
            del data[flag]

        record = SpotifyPlayRecord.from_json(data)

        assert getattr(record, flag) is False

    def test_invalid_timestamp_raises_value_error(self):
        with pytest.raises(ValueError):
            SpotifyPlayRecord.from_json(_make_valid_record(ts="not-a-date"))


class TestParseSpotifyPersonalData:
    """Test parse_spotify_personal_data() file parsing."""

    def test_multiple_records_parsed(self, tmp_path: Path):
        data = [
            _make_valid_record(master_metadata_track_name="Song A"),
            _make_valid_record(master_metadata_track_name="Song B"),
            _make_valid_record(master_metadata_track_name="Song C"),
        ]
        file = tmp_path / "history.json"
        file.write_text(json.dumps(data))

        records = parse_spotify_personal_data(file)
        assert len(records) == 3
        assert [r.track_name for r in records] == ["Song A", "Song B", "Song C"]

    def test_records_without_track_uri_filtered_out(self, tmp_path: Path):
        """Non-music content (podcasts) lack spotify_track_uri and should be skipped."""
        data = [
            _make_valid_record(),
            {**_make_valid_record(), "spotify_track_uri": None},  # podcast
            {**_make_valid_record(), "spotify_track_uri": ""},  # empty URI
        ]
        file = tmp_path / "history.json"
        file.write_text(json.dumps(data))

        records = parse_spotify_personal_data(file)
        assert len(records) == 1

    def test_records_without_track_name_filtered_out(self, tmp_path: Path):
        data = [
            _make_valid_record(),
            {**_make_valid_record(), "master_metadata_track_name": None},
        ]
        file = tmp_path / "history.json"
        file.write_text(json.dumps(data))

        records = parse_spotify_personal_data(file)
        assert len(records) == 1

    def test_malformed_records_skipped_gracefully(self, tmp_path: Path):
        """Malformed records should be skipped, not crash the entire parse."""
        data = [
            _make_valid_record(),  # valid
            {  # malformed: has URI+name but missing ms_played
                "spotify_track_uri": "spotify:track:abc",
                "master_metadata_track_name": "Bad Track",
                "master_metadata_album_artist_name": "Artist",
                "master_metadata_album_album_name": "Album",
                "ts": "2024-01-01T00:00:00Z",
                # ms_played missing → KeyError
            },
            _make_valid_record(master_metadata_track_name="Valid After Bad"),  # valid
        ]
        file = tmp_path / "history.json"
        file.write_text(json.dumps(data))

        records = parse_spotify_personal_data(file)
        assert len(records) == 2
        assert records[0].track_name == "Test Song"
        assert records[1].track_name == "Valid After Bad"

    def test_file_not_found_raises(self):
        with pytest.raises(FileNotFoundError):
            parse_spotify_personal_data(Path("/nonexistent/file.json"))
