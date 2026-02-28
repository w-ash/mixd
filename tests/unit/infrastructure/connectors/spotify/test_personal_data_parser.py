"""Unit tests for Spotify personal data parser.

Tests the JSON parsing pipeline: raw Spotify export → SpotifyPlayRecord objects.
Covers happy path, malformed records, null-safety for optional fields, and edge cases.
"""

from datetime import datetime
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

    def test_timestamp_parsed_as_datetime(self):
        record = SpotifyPlayRecord.from_json(_make_valid_record())
        assert isinstance(record.timestamp, datetime)
        assert record.timestamp.year == 2024
        assert record.timestamp.month == 6
        assert record.timestamp.day == 15

    def test_missing_core_field_ts_raises_key_error(self):
        data = _make_valid_record()
        del data["ts"]
        with pytest.raises(KeyError):
            SpotifyPlayRecord.from_json(data)

    def test_missing_core_field_track_uri_raises_key_error(self):
        data = _make_valid_record()
        del data["spotify_track_uri"]
        with pytest.raises(KeyError):
            SpotifyPlayRecord.from_json(data)

    def test_missing_core_field_track_name_raises_key_error(self):
        data = _make_valid_record()
        del data["master_metadata_track_name"]
        with pytest.raises(KeyError):
            SpotifyPlayRecord.from_json(data)

    def test_missing_core_field_ms_played_raises_key_error(self):
        data = _make_valid_record()
        del data["ms_played"]
        with pytest.raises(KeyError):
            SpotifyPlayRecord.from_json(data)

    # Null-safety for optional behavioral fields
    def test_missing_platform_defaults_to_unknown(self):
        data = _make_valid_record()
        del data["platform"]
        record = SpotifyPlayRecord.from_json(data)
        assert record.platform == "unknown"

    def test_missing_country_defaults_to_unknown(self):
        data = _make_valid_record()
        del data["conn_country"]
        record = SpotifyPlayRecord.from_json(data)
        assert record.country == "unknown"

    def test_missing_reason_start_defaults_to_unknown(self):
        data = _make_valid_record()
        del data["reason_start"]
        record = SpotifyPlayRecord.from_json(data)
        assert record.reason_start == "unknown"

    def test_missing_reason_end_defaults_to_unknown(self):
        data = _make_valid_record()
        del data["reason_end"]
        record = SpotifyPlayRecord.from_json(data)
        assert record.reason_end == "unknown"

    def test_none_skipped_defaults_to_false(self):
        """Spotify exports can have null for skipped field."""
        record = SpotifyPlayRecord.from_json(_make_valid_record(skipped=None))
        assert record.skipped is False

    def test_none_shuffle_defaults_to_false(self):
        record = SpotifyPlayRecord.from_json(_make_valid_record(shuffle=None))
        assert record.shuffle is False

    def test_none_offline_defaults_to_false(self):
        record = SpotifyPlayRecord.from_json(_make_valid_record(offline=None))
        assert record.offline is False

    def test_none_incognito_mode_defaults_to_false(self):
        record = SpotifyPlayRecord.from_json(_make_valid_record(incognito_mode=None))
        assert record.incognito_mode is False

    def test_missing_shuffle_defaults_to_false(self):
        data = _make_valid_record()
        del data["shuffle"]
        record = SpotifyPlayRecord.from_json(data)
        assert record.shuffle is False

    def test_missing_skipped_defaults_to_false(self):
        data = _make_valid_record()
        del data["skipped"]
        record = SpotifyPlayRecord.from_json(data)
        assert record.skipped is False

    def test_invalid_timestamp_raises_value_error(self):
        with pytest.raises(ValueError):
            SpotifyPlayRecord.from_json(_make_valid_record(ts="not-a-date"))


class TestParseSpotifyPersonalData:
    """Test parse_spotify_personal_data() file parsing."""

    def test_valid_file_returns_records(self, tmp_path: Path):
        file = tmp_path / "history.json"
        file.write_text(json.dumps([_make_valid_record()]))

        records = parse_spotify_personal_data(file)

        assert len(records) == 1
        assert records[0].track_name == "Test Song"

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

    def test_empty_file_returns_empty_list(self, tmp_path: Path):
        file = tmp_path / "history.json"
        file.write_text("[]")

        records = parse_spotify_personal_data(file)
        assert records == []

    def test_file_not_found_raises(self):
        with pytest.raises(FileNotFoundError):
            parse_spotify_personal_data(Path("/nonexistent/file.json"))

    def test_large_mixed_file_processes_correctly(self, tmp_path: Path):
        """Mixed valid/invalid records in a larger file should all process."""
        valid = [
            _make_valid_record(master_metadata_track_name=f"Track {i}")
            for i in range(50)
        ]
        invalid = [
            {
                "spotify_track_uri": "spotify:track:x",
                "master_metadata_track_name": "Bad",
            }
            for _ in range(10)
        ]
        # Podcasts (no URI)
        podcasts = [
            {"master_metadata_track_name": None, "spotify_track_uri": None}
            for _ in range(5)
        ]

        all_data = valid + invalid + podcasts
        file = tmp_path / "history.json"
        file.write_text(json.dumps(all_data))

        records = parse_spotify_personal_data(file)
        assert len(records) == 50
