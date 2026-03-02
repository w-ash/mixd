"""Characterization tests for metric-based transforms.

Locks down filter_by_metric_range and sort_by_external_metrics behavior
before renaming and refactoring.
"""

from src.application.metadata_transforms.metric_transforms import (
    filter_by_metric_range,
    sort_by_external_metrics,
)
from src.domain.entities.track import TrackList
from tests.fixtures.factories import make_tracks


class TestFilterByMetricRange:
    """Tests for filter_by_metric_range transform."""

    def test_filter_min_bound(self):
        """Tracks below min_value are excluded."""
        tracks = make_tracks(count=3)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"popularity": {1: 10, 2: 50, 3: 90}}},
        )
        result = filter_by_metric_range("popularity", min_value=40, tracklist=tl)
        assert {t.id for t in result.tracks} == {2, 3}

    def test_filter_max_bound(self):
        """Tracks above max_value are excluded."""
        tracks = make_tracks(count=3)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"popularity": {1: 10, 2: 50, 3: 90}}},
        )
        result = filter_by_metric_range("popularity", max_value=60, tracklist=tl)
        assert {t.id for t in result.tracks} == {1, 2}

    def test_filter_min_and_max_bounds(self):
        """Only tracks within [min, max] range are kept."""
        tracks = make_tracks(count=3)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"popularity": {1: 10, 2: 50, 3: 90}}},
        )
        result = filter_by_metric_range(
            "popularity", min_value=20, max_value=80, tracklist=tl
        )
        assert {t.id for t in result.tracks} == {2}

    def test_include_missing_true(self):
        """Tracks without metric values are included when include_missing=True."""
        tracks = make_tracks(count=3)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"popularity": {1: 50}}},
        )
        result = filter_by_metric_range(
            "popularity", min_value=40, include_missing=True, tracklist=tl
        )
        # Track 1 passes (50 >= 40), tracks 2,3 included (missing)
        assert {t.id for t in result.tracks} == {1, 2, 3}

    def test_include_missing_false(self):
        """Tracks without metric values are excluded when include_missing=False."""
        tracks = make_tracks(count=3)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"popularity": {1: 50}}},
        )
        result = filter_by_metric_range(
            "popularity", min_value=40, include_missing=False, tracklist=tl
        )
        assert {t.id for t in result.tracks} == {1}

    def test_factory_mode_returns_callable(self):
        """Factory mode returns a callable transform."""
        transform = filter_by_metric_range("popularity", min_value=40)
        assert callable(transform)

        tracks = make_tracks(count=2)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"popularity": {1: 10, 2: 50}}},
        )
        result = transform(tl)
        assert {t.id for t in result.tracks} == {2}

    def test_preserves_existing_metadata(self):
        """Filter operation preserves existing tracklist metadata."""
        tracks = make_tracks(count=3)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"popularity": {1: 10, 2: 50, 3: 90}}},
        )
        result = filter_by_metric_range("popularity", min_value=40, tracklist=tl)
        assert "metrics" in result.metadata
        assert len(result.tracks) == 2


class TestSortByExternalMetrics:
    """Tests for sort_by_external_metrics transform."""

    def test_sort_descending(self):
        """Highest metric values first when reverse=True."""
        tracks = make_tracks(count=3)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"popularity": {1: 10, 2: 90, 3: 50}}},
        )
        result = sort_by_external_metrics("popularity", reverse=True, tracklist=tl)
        assert [t.id for t in result.tracks] == [2, 3, 1]

    def test_sort_ascending(self):
        """Lowest metric values first when reverse=False."""
        tracks = make_tracks(count=3)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"popularity": {1: 10, 2: 90, 3: 50}}},
        )
        result = sort_by_external_metrics("popularity", reverse=False, tracklist=tl)
        assert [t.id for t in result.tracks] == [1, 3, 2]

    def test_missing_metrics_sort_to_end_descending(self):
        """Tracks without metrics sort to end in descending order."""
        tracks = make_tracks(count=3)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"popularity": {1: 50}}},
        )
        result = sort_by_external_metrics("popularity", reverse=True, tracklist=tl)
        assert result.tracks[0].id == 1
        # Tracks 2 and 3 are at the end (order between them is stable)
        assert {t.id for t in result.tracks[1:]} == {2, 3}

    def test_missing_metrics_sort_to_end_ascending(self):
        """Tracks without metrics sort to end in ascending order."""
        tracks = make_tracks(count=3)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"popularity": {1: 50}}},
        )
        result = sort_by_external_metrics("popularity", reverse=False, tracklist=tl)
        assert result.tracks[0].id == 1
        assert {t.id for t in result.tracks[1:]} == {2, 3}

    def test_factory_mode_returns_callable(self):
        """Factory mode returns a callable transform."""
        transform = sort_by_external_metrics("popularity", reverse=True)
        assert callable(transform)

        tracks = make_tracks(count=2)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"popularity": {1: 10, 2: 90}}},
        )
        result = transform(tl)
        assert [t.id for t in result.tracks] == [2, 1]

    def test_default_reverse_is_true(self):
        """Default sorting is descending (reverse=True)."""
        transform = sort_by_external_metrics("popularity")
        tracks = make_tracks(count=2)
        tl = TrackList(
            tracks=tracks,
            metadata={"metrics": {"popularity": {1: 10, 2: 90}}},
        )
        result = transform(tl)
        assert [t.id for t in result.tracks] == [2, 1]
