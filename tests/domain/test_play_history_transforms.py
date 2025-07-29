"""Fast unit tests for play history transform functions."""

from datetime import UTC, datetime, timedelta

import pytest

from src.domain.entities.track import Artist, Track, TrackList
from src.domain.transforms.core import filter_by_play_history, sort_by_play_history, time_range_predicate


class TestTimeRangePredicate:
    """Test time range predicate creation."""

    def test_days_back_predicate(self):
        """Test predicate with min_days_back parameter."""
        predicate = time_range_predicate(days_back=30)
        
        recent_date = datetime.now(UTC) - timedelta(days=15)
        old_date = datetime.now(UTC) - timedelta(days=60)
        
        assert predicate(recent_date) is True
        assert predicate(old_date) is False

    def test_absolute_date_predicate(self):
        """Test predicate with absolute dates."""
        start_date = datetime(2024, 1, 1, tzinfo=UTC)
        end_date = datetime(2024, 3, 31, tzinfo=UTC)
        predicate = time_range_predicate(after_date=start_date, before_date=end_date)
        
        in_range = datetime(2024, 2, 15, tzinfo=UTC)
        before_range = datetime(2023, 12, 15, tzinfo=UTC)
        
        assert predicate(in_range) is True
        assert predicate(before_range) is False


class TestFilterByPlayHistory:
    """Test unified play history filtering."""

    def test_min_plays_filter(self):
        """Test filtering by minimum play count."""
        tracks = [
            Track(id=1, title="Popular", artists=[Artist(name="Artist 1")]),
            Track(id=2, title="Unpopular", artists=[Artist(name="Artist 2")]),
        ]
        
        metadata = {"total_plays": {1: 10, 2: 3}}
        tracklist = TrackList(tracks=tracks, metadata=metadata)
        
        result = filter_by_play_history(min_plays=5, tracklist=tracklist)
        
        assert len(result.tracks) == 1
        assert result.tracks[0].id == 1

    def test_play_count_range_filter(self):
        """Test filtering by play count range."""
        tracks = [
            Track(id=1, title="Low", artists=[Artist(name="Artist 1")]),
            Track(id=2, title="Medium", artists=[Artist(name="Artist 2")]),
            Track(id=3, title="High", artists=[Artist(name="Artist 3")]),
        ]
        
        metadata = {"total_plays": {1: 2, 2: 5, 3: 15}}
        tracklist = TrackList(tracks=tracks, metadata=metadata)
        
        result = filter_by_play_history(min_plays=3, max_plays=10, tracklist=tracklist)
        
        assert len(result.tracks) == 1
        assert result.tracks[0].id == 2

    def test_relative_time_window_filter(self):
        """Test filtering with relative time window (min_days_back/max_days_back)."""
        tracks = [
            Track(id=1, title="Recent", artists=[Artist(name="Artist 1")]),
            Track(id=2, title="Old", artists=[Artist(name="Artist 2")]),
        ]
        
        # Recent play (15 days ago) and old play (60 days ago)
        recent_date = (datetime.now(UTC) - timedelta(days=15)).isoformat()
        old_date = (datetime.now(UTC) - timedelta(days=60)).isoformat()
        
        metadata = {
            "total_plays": {1: 5, 2: 8},
            "last_played_dates": {1: recent_date, 2: old_date}
        }
        tracklist = TrackList(tracks=tracks, metadata=metadata)
        
        # Filter for tracks played within last 30 days
        result = filter_by_play_history(max_days_back=30, tracklist=tracklist)
        
        assert len(result.tracks) == 1
        assert result.tracks[0].id == 1

    def test_absolute_date_filter(self):
        """Test filtering with absolute date range (start_date/end_date)."""
        tracks = [
            Track(id=1, title="Winter Track", artists=[Artist(name="Artist 1")]),
            Track(id=2, title="Summer Track", artists=[Artist(name="Artist 2")]),
        ]
        
        metadata = {
            "total_plays": {1: 3, 2: 7},
            "last_played_dates": {
                1: "2024-01-15T00:00:00+00:00",
                2: "2024-07-15T00:00:00+00:00"
            }
        }
        tracklist = TrackList(tracks=tracks, metadata=metadata)
        
        # Filter for tracks played between June 1 and August 31, 2024
        result = filter_by_play_history(
            start_date="2024-06-01",
            end_date="2024-08-31",
            tracklist=tracklist
        )
        
        assert len(result.tracks) == 1
        assert result.tracks[0].id == 2

    def test_constraint_validation(self):
        """Test that at least one constraint is required."""
        tracks = [Track(id=1, title="Test", artists=[Artist(name="Artist")])]
        tracklist = TrackList(tracks=tracks, metadata={})
        
        with pytest.raises(ValueError, match="Must specify at least one constraint"):
            filter_by_play_history(tracklist=tracklist)

    def test_curry_partial_application(self):
        """Test currying works correctly."""
        popular_filter = filter_by_play_history(min_plays=10)
        assert callable(popular_filter)
        
        tracks = [Track(id=1, title="Popular", artists=[Artist(name="Artist")])]
        metadata = {"total_plays": {1: 15}}
        tracklist = TrackList(tracks=tracks, metadata=metadata)
        
        result = popular_filter(tracklist)
        assert len(result.tracks) == 1

    def test_hidden_gems_pattern(self):
        """Test hidden gems pattern: loved but not played recently."""
        tracks = [
            Track(id=1, title="Hidden Gem", artists=[Artist(name="Artist 1")]),
            Track(id=2, title="Current Favorite", artists=[Artist(name="Artist 2")]),
            Track(id=3, title="Rarely Played", artists=[Artist(name="Artist 3")]),
        ]
        
        # Hidden gem: 5 plays but last played 200 days ago
        # Current favorite: 8 plays, last played 10 days ago  
        # Rarely played: 1 play, last played 100 days ago
        old_date = (datetime.now(UTC) - timedelta(days=200)).isoformat()
        recent_date = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        moderate_date = (datetime.now(UTC) - timedelta(days=100)).isoformat()
        
        metadata = {
            "total_plays": {1: 5, 2: 8, 3: 1},
            "last_played_dates": {1: old_date, 2: recent_date, 3: moderate_date}
        }
        tracklist = TrackList(tracks=tracks, metadata=metadata)
        
        # Find hidden gems: at least 3 plays, but not played in last 180 days
        result = filter_by_play_history(
            min_plays=3, 
            min_days_back=180,
            tracklist=tracklist
        )
        
        assert len(result.tracks) == 1
        assert result.tracks[0].id == 1  # The hidden gem

    def test_current_obsessions_pattern(self):
        """Test current obsessions pattern: heavy recent rotation."""
        tracks = [
            Track(id=1, title="Current Obsession", artists=[Artist(name="Artist 1")]),
            Track(id=2, title="Old Favorite", artists=[Artist(name="Artist 2")]),
        ]
        
        # Current obsession: 10 plays in last 20 days
        # Old favorite: 15 plays but last played 100 days ago
        recent_date = (datetime.now(UTC) - timedelta(days=20)).isoformat()
        old_date = (datetime.now(UTC) - timedelta(days=100)).isoformat()
        
        metadata = {
            "total_plays": {1: 10, 2: 15},
            "last_played_dates": {1: recent_date, 2: old_date}
        }
        tracklist = TrackList(tracks=tracks, metadata=metadata)
        
        # Find current obsessions: 8+ plays in last 30 days
        result = filter_by_play_history(
            min_plays=8,
            max_days_back=30,
            tracklist=tracklist
        )
        
        assert len(result.tracks) == 1
        assert result.tracks[0].id == 1  # The current obsession


class TestSortByPlayHistory:
    """Test play history sorting functionality."""

    def test_all_time_sort_descending(self):
        """Test sorting by all-time play count (most played first)."""
        tracks = [
            Track(id=1, title="Low Plays", artists=[Artist(name="Artist 1")]),
            Track(id=2, title="High Plays", artists=[Artist(name="Artist 2")]),
            Track(id=3, title="Medium Plays", artists=[Artist(name="Artist 3")]),
        ]
        
        metadata = {"total_plays": {1: 5, 2: 20, 3: 12}}
        tracklist = TrackList(tracks=tracks, metadata=metadata)
        
        result = sort_by_play_history(reverse=True, tracklist=tracklist)
        
        # Should be sorted by play count: 20, 12, 5
        assert len(result.tracks) == 3
        assert result.tracks[0].id == 2  # High Plays (20)
        assert result.tracks[1].id == 3  # Medium Plays (12)
        assert result.tracks[2].id == 1  # Low Plays (5)

    def test_all_time_sort_ascending(self):
        """Test sorting by all-time play count (least played first)."""
        tracks = [
            Track(id=1, title="Low Plays", artists=[Artist(name="Artist 1")]),
            Track(id=2, title="High Plays", artists=[Artist(name="Artist 2")]),
            Track(id=3, title="Medium Plays", artists=[Artist(name="Artist 3")]),
        ]
        
        metadata = {"total_plays": {1: 5, 2: 20, 3: 12}}
        tracklist = TrackList(tracks=tracks, metadata=metadata)
        
        result = sort_by_play_history(reverse=False, tracklist=tracklist)
        
        # Should be sorted by play count: 5, 12, 20
        assert len(result.tracks) == 3
        assert result.tracks[0].id == 1  # Low Plays (5)
        assert result.tracks[1].id == 3  # Medium Plays (12)
        assert result.tracks[2].id == 2  # High Plays (20)

    def test_time_window_sort(self):
        """Test sorting within a time window."""
        tracks = [
            Track(id=1, title="Recent Track", artists=[Artist(name="Artist 1")]),
            Track(id=2, title="Old Track", artists=[Artist(name="Artist 2")]),
            Track(id=3, title="Another Recent", artists=[Artist(name="Artist 3")]),
        ]
        
        # Recent tracks (within 30 days) and old track (60 days ago)
        recent_date = (datetime.now(UTC) - timedelta(days=15)).isoformat()
        recent_date2 = (datetime.now(UTC) - timedelta(days=20)).isoformat()
        old_date = (datetime.now(UTC) - timedelta(days=60)).isoformat()
        
        metadata = {
            "total_plays": {1: 10, 2: 25, 3: 15},  # Old track has highest total plays
            "last_played_dates": {1: recent_date, 2: old_date, 3: recent_date2}
        }
        tracklist = TrackList(tracks=tracks, metadata=metadata)
        
        # Sort by plays within last 30 days (reverse=True, most played first)
        result = sort_by_play_history(max_days_back=30, reverse=True, tracklist=tracklist)
        
        # Only recent tracks should have non-zero sort values
        # Track 1: 10 plays (recent), Track 3: 15 plays (recent), Track 2: 0 plays (too old)
        assert len(result.tracks) == 3
        assert result.tracks[0].id == 3  # 15 plays (recent)
        assert result.tracks[1].id == 1  # 10 plays (recent)
        assert result.tracks[2].id == 2  # 0 plays (too old for window)

    def test_absolute_date_sort(self):
        """Test sorting with absolute date range."""
        tracks = [
            Track(id=1, title="Summer Track", artists=[Artist(name="Artist 1")]),
            Track(id=2, title="Winter Track", artists=[Artist(name="Artist 2")]),
            Track(id=3, title="Spring Track", artists=[Artist(name="Artist 3")]),
        ]
        
        metadata = {
            "total_plays": {1: 8, 2: 12, 3: 6},
            "last_played_dates": {
                1: "2024-07-15T00:00:00+00:00",  # Summer (in range)
                2: "2024-01-15T00:00:00+00:00",  # Winter (out of range)
                3: "2024-06-15T00:00:00+00:00",  # Spring (in range)
            }
        }
        tracklist = TrackList(tracks=tracks, metadata=metadata)
        
        # Sort by plays between June 1 and August 31, 2024
        result = sort_by_play_history(
            start_date="2024-06-01",
            end_date="2024-08-31",
            reverse=True,
            tracklist=tracklist
        )
        
        # Only summer (8 plays) and spring (6 plays) tracks should count
        assert len(result.tracks) == 3
        assert result.tracks[0].id == 1  # Summer Track (8 plays, in range)
        assert result.tracks[1].id == 3  # Spring Track (6 plays, in range)
        assert result.tracks[2].id == 2  # Winter Track (0 plays, out of range)

    def test_curry_partial_application(self):
        """Test currying works correctly for sorting."""
        most_played_sorter = sort_by_play_history(reverse=True)
        assert callable(most_played_sorter)
        
        tracks = [
            Track(id=1, title="Low", artists=[Artist(name="Artist 1")]),
            Track(id=2, title="High", artists=[Artist(name="Artist 2")]),
        ]
        metadata = {"total_plays": {1: 5, 2: 15}}
        tracklist = TrackList(tracks=tracks, metadata=metadata)
        
        result = most_played_sorter(tracklist)
        assert len(result.tracks) == 2
        assert result.tracks[0].id == 2  # High plays first
        assert result.tracks[1].id == 1  # Low plays second

    def test_tracks_without_play_data(self):
        """Test sorting handles tracks without play data gracefully."""
        tracks = [
            Track(id=1, title="With Plays", artists=[Artist(name="Artist 1")]),
            Track(id=2, title="No Data", artists=[Artist(name="Artist 2")]),
            Track(id=None, title="No ID", artists=[Artist(name="Artist 3")]),
        ]
        
        metadata = {"total_plays": {1: 10}}  # Only track 1 has play data
        tracklist = TrackList(tracks=tracks, metadata=metadata)
        
        result = sort_by_play_history(reverse=True, tracklist=tracklist)
        
        # Track with plays should be first, others should be ordered by their 0 values
        assert len(result.tracks) == 3
        assert result.tracks[0].id == 1  # Has plays (10)
        # Tracks 2 and None should follow (both have 0 plays, order preserved)