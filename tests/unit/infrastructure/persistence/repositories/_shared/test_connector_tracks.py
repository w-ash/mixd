"""Unit tests for the shared ``connector_tracks`` row helpers.

``extract_db_artist_names`` is the JSONB ``{"names": [...]}`` boundary narrower
used by the track mapper, the connector-track mapper, the playlist mapper and
the resolution recorder — its narrowing of the ``JsonValue`` union is pinned
here. ``build_connector_track_row`` is the single builder of the row shape all
three connector-track writers upsert; the pin is the exact column set, since a
row missing a key breaks a multi-row INSERT.

Both are pure (no DB, no session).
"""

from datetime import UTC, datetime

from src.infrastructure.persistence.repositories._shared.connector_tracks import (
    build_connector_track_row,
    extract_db_artist_names,
)


class TestExtractDbArtistNames:
    def test_extracts_string_names(self):
        assert extract_db_artist_names({"names": ["Radiohead", "Björk"]}) == [
            "Radiohead",
            "Björk",
        ]

    def test_missing_names_key_returns_empty(self):
        assert extract_db_artist_names({}) == []

    def test_names_not_a_list_returns_empty(self):
        # JsonValue union: a stray scalar under "names" must not blow up.
        assert extract_db_artist_names({"names": "Radiohead"}) == []

    def test_non_string_elements_filtered_out(self):
        # Defensive narrowing: only str elements survive.
        assert extract_db_artist_names({"names": ["ok", 123, None, "fine"]}) == [
            "ok",
            "fine",
        ]

    def test_empty_names_list_returns_empty(self):
        assert extract_db_artist_names({"names": []}) == []


class TestBuildConnectorTrackRow:
    def test_full_row_carries_every_column(self):
        released = datetime(2019, 4, 26, tzinfo=UTC)
        stamped = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)

        row = build_connector_track_row(
            "spotify",
            "sp_123",
            title="Gold Rush",
            artist_names=["Neon Priest", "Ada Vale"],
            album="Halogen",
            duration_ms=214_000,
            release_date=released,
            isrc="GBAAA1900001",
            raw_metadata={"popularity": 42},
            last_updated=stamped,
        )

        assert row == {
            "connector_name": "spotify",
            "connector_track_identifier": "sp_123",
            "title": "Gold Rush",
            "artists": {"names": ["Neon Priest", "Ada Vale"]},
            "album": "Halogen",
            "duration_ms": 214_000,
            "release_date": released,
            "isrc": "GBAAA1900001",
            "raw_metadata": {"popularity": 42},
            "last_updated": stamped,
        }

    def test_empty_artists_still_writes_the_names_envelope(self):
        # The column is NOT NULL-shaped by convention: readers call
        # extract_db_artist_names on it, so the envelope must always be there.
        row = build_connector_track_row(
            "lastfm",
            "lf_1",
            title="Untitled",
            artist_names=[],
            last_updated=datetime.now(UTC),
        )

        assert row["artists"] == {"names": []}

    def test_omitted_optionals_default_and_raw_metadata_becomes_empty_dict(self):
        row = build_connector_track_row(
            "lastfm",
            "lf_2",
            title="Untitled",
            artist_names=("Solo",),
            raw_metadata=None,
            last_updated=datetime.now(UTC),
        )

        assert row["raw_metadata"] == {}
        assert row["album"] is None
        assert row["duration_ms"] is None
        assert row["release_date"] is None
        assert row["isrc"] is None
