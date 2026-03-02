"""Tests for date-based sorting transforms (added_at, first_played, last_played)."""

from datetime import UTC, datetime

from src.application.metadata_transforms.metric_transforms import sort_by_date
from src.domain.entities.track import TrackList
from tests.fixtures.factories import make_track


class TestSortByAddedAt:
    def test_ascending_oldest_first(self):
        tracks = [make_track(id=1), make_track(id=2), make_track(id=3)]
        tl = TrackList(
            tracks=tracks,
            metadata={
                "added_at_dates": {
                    1: "2024-06-01T00:00:00+00:00",
                    2: "2024-01-01T00:00:00+00:00",
                    3: "2024-03-01T00:00:00+00:00",
                },
            },
        )

        result = sort_by_date("added_at", ascending=True, tracklist=tl)
        assert [t.id for t in result.tracks] == [2, 3, 1]

    def test_descending_newest_first(self):
        tracks = [make_track(id=1), make_track(id=2), make_track(id=3)]
        tl = TrackList(
            tracks=tracks,
            metadata={
                "added_at_dates": {
                    1: "2024-06-01T00:00:00+00:00",
                    2: "2024-01-01T00:00:00+00:00",
                    3: "2024-03-01T00:00:00+00:00",
                },
            },
        )

        result = sort_by_date("added_at", ascending=False, tracklist=tl)
        assert [t.id for t in result.tracks] == [1, 3, 2]

    def test_missing_dates_sort_to_end(self):
        tracks = [make_track(id=1), make_track(id=2), make_track(id=3)]
        tl = TrackList(
            tracks=tracks,
            metadata={
                "added_at_dates": {
                    1: "2024-06-01T00:00:00+00:00",
                    # track 2 has no added_at
                    3: "2024-03-01T00:00:00+00:00",
                },
            },
        )

        result = sort_by_date("added_at", ascending=True, tracklist=tl)
        # Track 2 should be at the end
        assert result.tracks[-1].id == 2
        assert [t.id for t in result.tracks[:2]] == [3, 1]

    def test_missing_dates_sort_to_end_descending(self):
        tracks = [make_track(id=1), make_track(id=2), make_track(id=3)]
        tl = TrackList(
            tracks=tracks,
            metadata={
                "added_at_dates": {
                    1: "2024-06-01T00:00:00+00:00",
                    3: "2024-03-01T00:00:00+00:00",
                },
            },
        )

        result = sort_by_date("added_at", ascending=False, tracklist=tl)
        assert result.tracks[-1].id == 2


class TestSortByFirstPlayed:
    def test_ascending(self):
        tracks = [make_track(id=1), make_track(id=2), make_track(id=3)]
        tl = TrackList(
            tracks=tracks,
            metadata={
                "metrics": {
                    "first_played_dates": {
                        1: datetime(2023, 6, 1, tzinfo=UTC),
                        2: datetime(2023, 1, 1, tzinfo=UTC),
                        3: datetime(2023, 9, 1, tzinfo=UTC),
                    },
                },
            },
        )

        result = sort_by_date("first_played", ascending=True, tracklist=tl)
        assert [t.id for t in result.tracks] == [2, 1, 3]

    def test_descending(self):
        tracks = [make_track(id=1), make_track(id=2), make_track(id=3)]
        tl = TrackList(
            tracks=tracks,
            metadata={
                "metrics": {
                    "first_played_dates": {
                        1: datetime(2023, 6, 1, tzinfo=UTC),
                        2: datetime(2023, 1, 1, tzinfo=UTC),
                        3: datetime(2023, 9, 1, tzinfo=UTC),
                    },
                },
            },
        )

        result = sort_by_date("first_played", ascending=False, tracklist=tl)
        assert [t.id for t in result.tracks] == [3, 1, 2]


class TestSortByLastPlayed:
    def test_ascending(self):
        tracks = [make_track(id=1), make_track(id=2), make_track(id=3)]
        tl = TrackList(
            tracks=tracks,
            metadata={
                "metrics": {
                    "last_played_dates": {
                        1: datetime(2024, 12, 1, tzinfo=UTC),
                        2: datetime(2024, 6, 1, tzinfo=UTC),
                        3: datetime(2024, 9, 1, tzinfo=UTC),
                    },
                },
            },
        )

        result = sort_by_date("last_played", ascending=True, tracklist=tl)
        assert [t.id for t in result.tracks] == [2, 3, 1]


class TestSortByDateEdgeCases:
    def test_empty_tracklist(self):
        result = sort_by_date("added_at", tracklist=TrackList())
        assert result.tracks == []

    def test_no_metadata(self):
        tracks = [make_track(id=1), make_track(id=2)]
        result = sort_by_date("added_at", tracklist=TrackList(tracks=tracks))
        # All tracks have no dates, so order is stable (all have same sentinel)
        assert len(result.tracks) == 2

    def test_handles_iso_strings_in_metrics(self):
        """Play history dates stored as ISO strings should be parsed correctly."""
        tracks = [make_track(id=1), make_track(id=2)]
        tl = TrackList(
            tracks=tracks,
            metadata={
                "metrics": {
                    "last_played_dates": {
                        1: "2024-12-01T00:00:00+00:00",
                        2: "2024-06-01T00:00:00+00:00",
                    },
                },
            },
        )

        result = sort_by_date("last_played", ascending=True, tracklist=tl)
        assert [t.id for t in result.tracks] == [2, 1]

    def test_dual_mode_returns_transform(self):
        """Calling without tracklist should return a callable transform."""
        transform = sort_by_date("added_at")
        assert callable(transform)

        tl = TrackList(
            tracks=[make_track(id=1)],
            metadata={"added_at_dates": {1: "2024-01-01T00:00:00+00:00"}},
        )
        result = transform(tl)
        assert len(result.tracks) == 1
