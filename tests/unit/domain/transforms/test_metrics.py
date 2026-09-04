"""Characterization tests for metric-based transforms.

Locks down filter_by_metric_range and sort_by_external_metrics behavior
before renaming and refactoring.
"""

from datetime import UTC, datetime

from src.domain.entities.track import TrackList
from src.domain.transforms.metrics import (
    filter_by_metric_range,
    sort_by_external_metrics,
)
from tests.fixtures.factories import make_tracks


def _metrics_for(tracks, values):
    """Build a metric dict keyed by track.id from a list of values."""
    return {t.id: v for t, v in zip(tracks, values, strict=False)}


class TestFilterByMetricRange:
    """Tests for filter_by_metric_range transform."""

    def test_filter_min_bound(self):
        """Tracks below min_value are excluded."""
        tracks = make_tracks(count=3)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"play_count": _metrics_for(tracks, [10, 50, 90])}},
        )
        result = filter_by_metric_range("play_count", min_value=40, tracklist=tl)
        assert {t.id for t in result.tracks} == {tracks[1].id, tracks[2].id}

    def test_filter_max_bound(self):
        """Tracks above max_value are excluded."""
        tracks = make_tracks(count=3)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"play_count": _metrics_for(tracks, [10, 50, 90])}},
        )
        result = filter_by_metric_range("play_count", max_value=60, tracklist=tl)
        assert {t.id for t in result.tracks} == {tracks[0].id, tracks[1].id}

    def test_filter_min_and_max_bounds(self):
        """Only tracks within [min, max] range are kept."""
        tracks = make_tracks(count=3)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"play_count": _metrics_for(tracks, [10, 50, 90])}},
        )
        result = filter_by_metric_range(
            "play_count", min_value=20, max_value=80, tracklist=tl
        )
        assert {t.id for t in result.tracks} == {tracks[1].id}

    def test_include_missing_true(self):
        """Tracks without metric values are included when include_missing=True."""
        tracks = make_tracks(count=3)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"play_count": {tracks[0].id: 50}}},
        )
        result = filter_by_metric_range(
            "play_count", min_value=40, include_missing=True, tracklist=tl
        )
        # Track 0 passes (50 >= 40), tracks 1,2 included (missing)
        assert {t.id for t in result.tracks} == {t.id for t in tracks}

    def test_include_missing_false(self):
        """Tracks without metric values are excluded when include_missing=False."""
        tracks = make_tracks(count=3)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"play_count": {tracks[0].id: 50}}},
        )
        result = filter_by_metric_range(
            "play_count", min_value=40, include_missing=False, tracklist=tl
        )
        assert {t.id for t in result.tracks} == {tracks[0].id}

    def test_factory_mode_returns_callable(self):
        """Factory mode returns a callable transform."""
        transform = filter_by_metric_range("play_count", min_value=40)
        assert callable(transform)

        tracks = make_tracks(count=2)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"play_count": _metrics_for(tracks, [10, 50])}},
        )
        result = transform(tl)
        assert {t.id for t in result.tracks} == {tracks[1].id}

    def test_preserves_existing_metadata(self):
        """Filter operation preserves existing tracklist metadata."""
        tracks = make_tracks(count=3)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"play_count": _metrics_for(tracks, [10, 50, 90])}},
        )
        result = filter_by_metric_range("play_count", min_value=40, tracklist=tl)
        assert "metrics" in result.metadata
        assert len(result.tracks) == 2


class TestSortByExternalMetrics:
    """Tests for sort_by_external_metrics transform."""

    def test_sort_descending(self):
        """Highest metric values first when reverse=True."""
        tracks = make_tracks(count=3)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"play_count": _metrics_for(tracks, [10, 90, 50])}},
        )
        result = sort_by_external_metrics("play_count", reverse=True, tracklist=tl)
        assert [t.id for t in result.tracks] == [
            tracks[1].id,
            tracks[2].id,
            tracks[0].id,
        ]

    def test_sort_ascending(self):
        """Lowest metric values first when reverse=False."""
        tracks = make_tracks(count=3)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"play_count": _metrics_for(tracks, [10, 90, 50])}},
        )
        result = sort_by_external_metrics("play_count", reverse=False, tracklist=tl)
        assert [t.id for t in result.tracks] == [
            tracks[0].id,
            tracks[2].id,
            tracks[1].id,
        ]

    def test_missing_metrics_sort_to_end_descending(self):
        """Tracks without metrics sort to end in descending order."""
        tracks = make_tracks(count=3)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"play_count": {tracks[0].id: 50}}},
        )
        result = sort_by_external_metrics("play_count", reverse=True, tracklist=tl)
        assert result.tracks[0].id == tracks[0].id
        # Tracks 1 and 2 are at the end (order between them is stable)
        assert {t.id for t in result.tracks[1:]} == {tracks[1].id, tracks[2].id}

    def test_missing_metrics_sort_to_end_ascending(self):
        """Tracks without metrics sort to end in ascending order."""
        tracks = make_tracks(count=3)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"play_count": {tracks[0].id: 50}}},
        )
        result = sort_by_external_metrics("play_count", reverse=False, tracklist=tl)
        assert result.tracks[0].id == tracks[0].id
        assert {t.id for t in result.tracks[1:]} == {tracks[1].id, tracks[2].id}

    def test_datetime_metric_sorts_and_missing_values_land_at_end(self):
        """Datetime-valued metrics sort; unenriched tracks land at the end."""
        tracks = make_tracks(count=4)
        values = {
            tracks[0].id: datetime(2026, 1, 5, tzinfo=UTC),
            tracks[1].id: datetime(2026, 3, 5, tzinfo=UTC),
            tracks[2].id: None,
        }
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"last_played_dates": values}},
        )

        newest_first = sort_by_external_metrics(
            "last_played_dates", reverse=True, tracklist=tl
        )
        assert [t.id for t in newest_first.tracks[:2]] == [
            tracks[1].id,
            tracks[0].id,
        ]
        assert {t.id for t in newest_first.tracks[2:]} == {
            tracks[2].id,
            tracks[3].id,
        }

        oldest_first = sort_by_external_metrics(
            "last_played_dates", reverse=False, tracklist=tl
        )
        assert [t.id for t in oldest_first.tracks[:2]] == [
            tracks[0].id,
            tracks[1].id,
        ]
        assert {t.id for t in oldest_first.tracks[2:]} == {
            tracks[2].id,
            tracks[3].id,
        }

    def test_factory_mode_returns_callable(self):
        """Factory mode returns a callable transform."""
        transform = sort_by_external_metrics("play_count", reverse=True)
        assert callable(transform)

        tracks = make_tracks(count=2)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"play_count": _metrics_for(tracks, [10, 90])}},
        )
        result = transform(tl)
        assert [t.id for t in result.tracks] == [tracks[1].id, tracks[0].id]

    def test_default_reverse_is_true(self):
        """Default sorting is descending (reverse=True)."""
        transform = sort_by_external_metrics("play_count")
        tracks = make_tracks(count=2)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"play_count": _metrics_for(tracks, [10, 90])}},
        )
        result = transform(tl)
        assert [t.id for t in result.tracks] == [tracks[1].id, tracks[0].id]
