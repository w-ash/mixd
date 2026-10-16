"""Tests for application transform helpers."""

from datetime import UTC, datetime

import pytest

from src.domain.entities.track import TrackList
from src.domain.transforms._metadata_helpers import (
    get_play_metrics,
    parse_datetime_safe,
)
from tests.fixtures.factories import make_tracks


class TestGetPlayMetrics:
    """Test play count and last-played extraction from TrackList metadata."""

    def test_nested_metrics_path(self):
        """Primary path: metrics stored under metadata["metrics"]["total_plays"]."""
        tl = TrackList(
            tracks=make_tracks(count=2),
            metadata={
                "metrics": {
                    "total_plays": {1: 10, 2: 20},
                    "last_played_dates": {
                        1: "2025-01-01T00:00:00+00:00",
                        2: "2025-06-15T00:00:00+00:00",
                    },
                }
            },
        )

        play_counts, last_played = get_play_metrics(tl)

        assert play_counts == {1: 10, 2: 20}
        assert last_played[1] == "2025-01-01T00:00:00+00:00"

    def test_empty_metadata_returns_empty_dicts(self):
        tl = TrackList(tracks=make_tracks(count=1), metadata={})

        play_counts, last_played = get_play_metrics(tl)

        assert play_counts == {}
        assert last_played == {}


class TestParseDatetimeSafe:
    """Test datetime parsing helper used by play history transforms."""

    @pytest.mark.parametrize(
        "value", ["2025-06-15T12:00:00+00:00", "2025-06-15T12:00:00"]
    )
    def test_iso_string_parses_to_utc(self, value: str):
        """An offset-less ISO string is read as UTC, not local time."""
        assert parse_datetime_safe(value) == datetime(2025, 6, 15, 12, tzinfo=UTC)

    def test_naive_datetime_gets_utc(self):
        naive = datetime(2025, 1, 1, 9, 30)  # ruff:ignore[call-datetime-without-tzinfo] — intentionally naive for testing

        result = parse_datetime_safe(naive)

        assert result == datetime(2025, 1, 1, 9, 30, tzinfo=UTC)
        assert result.tzinfo == UTC

    def test_aware_datetime_passthrough(self):
        aware = datetime(2025, 1, 1, tzinfo=UTC)

        result = parse_datetime_safe(aware)

        assert result == aware

    def test_none_returns_none(self):
        assert parse_datetime_safe(None) is None

    def test_invalid_string_returns_none(self):
        assert parse_datetime_safe("not-a-date") is None

    def test_empty_string_returns_none(self):
        assert parse_datetime_safe("") is None
