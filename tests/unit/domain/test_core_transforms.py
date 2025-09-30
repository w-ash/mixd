"""Tests for core domain transformations.

Tests the pure functional transformations in domain/transforms/core.py,
focusing on sorting by track attributes vs external metrics.
"""

from datetime import UTC, datetime

import pytest

from src.application.workflows.transform_registry import TRANSFORM_REGISTRY
from src.domain.entities.track import Artist, Track, TrackList


@pytest.mark.unit
class TestTrackAttributeSorting:
    """Test sorting by track attributes (title, artist, etc.) vs external metrics."""

    def test_sort_by_title_attribute_directly(self):
        """Test sorting by track title using track attribute directly."""
        # Arrange: Create tracks with different titles
        tracks = [
            Track(title="Zebra", artists=[Artist(name="Artist1")], id=1),
            Track(title="Apple", artists=[Artist(name="Artist2")], id=2),
            Track(title="Banana", artists=[Artist(name="Artist3")], id=3),
        ]
        tracklist = TrackList(tracks=tracks)

        # Act: Sort by title using transform registry
        sorter_fn = TRANSFORM_REGISTRY["sorter"]["by_metric"](
            _ctx=None, cfg={"metric_name": "title", "reverse": False}
        )
        sorted_tracklist = sorter_fn(tracklist)

        # Assert: Tracks should be sorted alphabetically by title
        assert len(sorted_tracklist.tracks) == 3
        assert sorted_tracklist.tracks[0].title == "Apple"
        assert sorted_tracklist.tracks[1].title == "Banana"
        assert sorted_tracklist.tracks[2].title == "Zebra"

        # Assert: Metrics should be populated in tracklist metadata
        title_metrics = sorted_tracklist.metadata["metrics"]["title"]
        assert title_metrics[1] == "Zebra"  # track id 1
        assert title_metrics[2] == "Apple"  # track id 2
        assert title_metrics[3] == "Banana"  # track id 3

    def test_sort_by_artist_attribute_directly(self):
        """Test sorting by primary artist name using track attribute directly."""
        # Arrange: Create tracks with different artists
        tracks = [
            Track(title="Song1", artists=[Artist(name="Zebra Band")], id=1),
            Track(title="Song2", artists=[Artist(name="Apple Band")], id=2),
            Track(title="Song3", artists=[Artist(name="Banana Band")], id=3),
        ]
        tracklist = TrackList(tracks=tracks)

        # Act: Sort by artist using transform registry
        sorter_fn = TRANSFORM_REGISTRY["sorter"]["by_metric"](
            _ctx=None, cfg={"metric_name": "artist", "reverse": False}
        )
        sorted_tracklist = sorter_fn(tracklist)

        # Assert: Tracks should be sorted alphabetically by artist
        assert len(sorted_tracklist.tracks) == 3
        assert sorted_tracklist.tracks[0].artists[0].name == "Apple Band"
        assert sorted_tracklist.tracks[1].artists[0].name == "Banana Band"
        assert sorted_tracklist.tracks[2].artists[0].name == "Zebra Band"

    def test_sort_by_release_date_attribute_directly(self):
        """Test sorting by release date using track attribute directly."""
        # Arrange: Create tracks with different release dates
        date1 = datetime(2020, 1, 1, tzinfo=UTC)
        date2 = datetime(2021, 1, 1, tzinfo=UTC)
        date3 = datetime(2019, 1, 1, tzinfo=UTC)

        tracks = [
            Track(
                title="Song1",
                artists=[Artist(name="Artist1")],
                release_date=date1,
                id=1,
            ),
            Track(
                title="Song2",
                artists=[Artist(name="Artist2")],
                release_date=date2,
                id=2,
            ),
            Track(
                title="Song3",
                artists=[Artist(name="Artist3")],
                release_date=date3,
                id=3,
            ),
        ]
        tracklist = TrackList(tracks=tracks)

        # Act: Sort by release date using transform registry
        sorter_fn = TRANSFORM_REGISTRY["sorter"]["by_metric"](
            _ctx=None, cfg={"metric_name": "release_date", "reverse": False}
        )
        sorted_tracklist = sorter_fn(tracklist)

        # Assert: Tracks should be sorted chronologically
        assert len(sorted_tracklist.tracks) == 3
        assert sorted_tracklist.tracks[0].release_date == date3  # 2019
        assert sorted_tracklist.tracks[1].release_date == date1  # 2020
        assert sorted_tracklist.tracks[2].release_date == date2  # 2021

    def test_sort_by_external_metric_from_metadata(self):
        """Test sorting by external metric (existing behavior should work)."""
        # Arrange: Create tracks with external metric values in metadata
        tracks = [
            Track(title="Song1", artists=[Artist(name="Artist1")], id=1),
            Track(title="Song2", artists=[Artist(name="Artist2")], id=2),
            Track(title="Song3", artists=[Artist(name="Artist3")], id=3),
        ]

        # Add external metrics to tracklist metadata
        external_metrics = {
            "lastfm_user_playcount": {
                1: 50,  # track id 1 has 50 plays
                2: 100,  # track id 2 has 100 plays
                3: 25,  # track id 3 has 25 plays
            }
        }
        tracklist = TrackList(tracks=tracks, metadata={"metrics": external_metrics})

        # Act: Sort by external metric using transform registry
        sorter_fn = TRANSFORM_REGISTRY["sorter"]["by_metric"](
            _ctx=None, cfg={"metric_name": "lastfm_user_playcount", "reverse": True}
        )
        sorted_tracklist = sorter_fn(tracklist)

        # Assert: Tracks should be sorted by play count (highest first)
        assert len(sorted_tracklist.tracks) == 3
        assert sorted_tracklist.tracks[0].id == 2  # 100 plays
        assert sorted_tracklist.tracks[1].id == 1  # 50 plays
        assert sorted_tracklist.tracks[2].id == 3  # 25 plays


@pytest.mark.unit
class TestWeightedShuffleSorting:
    """Test the weighted shuffle sorter functionality."""

    def test_weighted_shuffle_boundaries(self):
        """Test weighted shuffle at boundary values 0.0 and 1.0."""
        # Arrange: Create tracks in specific order
        tracks = [
            Track(title="First", artists=[Artist(name="Artist1")], id=1),
            Track(title="Second", artists=[Artist(name="Artist2")], id=2),
            Track(title="Third", artists=[Artist(name="Artist3")], id=3),
        ]
        original_order = [t.title for t in tracks]
        tracklist = TrackList(tracks=tracks)

        # Test strength 0.0 - should preserve original order
        sorter_fn = TRANSFORM_REGISTRY["sorter"]["weighted_shuffle"](
            _ctx=None, cfg={"shuffle_strength": 0.0}
        )
        result = sorter_fn(tracklist)
        assert [t.title for t in result.tracks] == original_order

        # Test strength 1.0 - should randomize (test multiple times for confidence)
        sorter_fn = TRANSFORM_REGISTRY["sorter"]["weighted_shuffle"](
            _ctx=None, cfg={"shuffle_strength": 1.0}
        )
        different_count = 0
        for _ in range(5):
            result = sorter_fn(tracklist)
            if [t.title for t in result.tracks] != original_order:
                different_count += 1

        # Should get different order most of the time
        assert different_count >= 3, (
            f"Expected mostly different orders, got {different_count}/5"
        )

        # All tracks should be preserved
        assert {t.id for t in result.tracks} == {1, 2, 3}

    def test_weighted_shuffle_invalid_bounds(self):
        """Test weighted shuffle rejects invalid strength values."""
        tracks = [Track(title="Test", artists=[Artist(name="Test")], id=1)]
        tracklist = TrackList(tracks=tracks)

        for invalid_strength in [-0.1, 1.1]:
            with pytest.raises(ValueError):
                sorter_fn = TRANSFORM_REGISTRY["sorter"]["weighted_shuffle"](
                    _ctx=None, cfg={"shuffle_strength": invalid_strength}
                )
                sorter_fn(tracklist)
