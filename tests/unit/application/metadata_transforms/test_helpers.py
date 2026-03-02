"""Tests for application transform helpers."""

from datetime import UTC, datetime

from src.application.metadata_transforms._helpers import (
    get_play_metrics,
    parse_datetime_safe,
)
from src.domain.entities.track import TrackList
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

    def test_iso_string(self):
        result = parse_datetime_safe("2025-06-15T12:00:00+00:00")

        assert isinstance(result, datetime)
        assert result.year == 2025
        assert result.tzinfo is not None

    def test_naive_datetime_gets_utc(self):
        naive = datetime(2025, 1, 1)  # noqa: DTZ001 — intentionally naive for testing

        result = parse_datetime_safe(naive)

        assert result is not None
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
