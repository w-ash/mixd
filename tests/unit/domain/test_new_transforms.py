"""Tests for new domain transforms: reverse, duration, liked status, intersect, percentage."""

from datetime import UTC, datetime

from src.domain.entities.track import Track, TrackList
from src.domain.transforms import (
    filter_by_duration,
    filter_by_liked_status,
    filter_by_release_year,
    intersect,
    reverse_tracks,
    select_by_percentage,
)
from tests.fixtures import make_track


class TestReverseTransform:
    def test_reverse_tracks(self):
        tracks = [make_track(i, f"Track {i}") for i in range(1, 4)]
        tl = TrackList(tracks=tracks)

        result = reverse_tracks(tracklist=tl)

        assert [t.id for t in result.tracks] == [3, 2, 1]

    def test_reverse_empty(self):
        result = reverse_tracks(tracklist=TrackList())
        assert result.tracks == []

    def test_reverse_single(self):
        tl = TrackList(tracks=[make_track(1)])
        result = reverse_tracks(tracklist=tl)
        assert len(result.tracks) == 1
        assert result.tracks[0].id == 1

    def test_reverse_preserves_metadata(self):
        tl = TrackList(
            tracks=[make_track(1), make_track(2)],
            metadata={"source_playlist_name": "test"},
        )
        result = reverse_tracks(tracklist=tl)
        assert result.metadata.get("source_playlist_name") == "test"


class TestFilterByDuration:
    def test_min_duration(self):
        tracks = [
            make_track(1, duration_ms=30_000),  # 30s
            make_track(2, duration_ms=120_000),  # 2min
            make_track(3, duration_ms=300_000),  # 5min
        ]
        result = filter_by_duration(min_ms=60_000, tracklist=TrackList(tracks=tracks))
        assert [t.id for t in result.tracks] == [2, 3]

    def test_max_duration(self):
        tracks = [
            make_track(1, duration_ms=30_000),
            make_track(2, duration_ms=120_000),
            make_track(3, duration_ms=600_000),  # 10min
        ]
        result = filter_by_duration(max_ms=300_000, tracklist=TrackList(tracks=tracks))
        assert [t.id for t in result.tracks] == [1, 2]

    def test_duration_range(self):
        tracks = [
            make_track(1, duration_ms=30_000),
            make_track(2, duration_ms=120_000),
            make_track(3, duration_ms=600_000),
        ]
        result = filter_by_duration(
            min_ms=60_000, max_ms=300_000, tracklist=TrackList(tracks=tracks)
        )
        assert [t.id for t in result.tracks] == [2]

    def test_missing_duration_excluded_by_default(self):
        tracks = [
            make_track(1, duration_ms=120_000),
            make_track(2, duration_ms=None),
        ]
        result = filter_by_duration(min_ms=60_000, tracklist=TrackList(tracks=tracks))
        assert [t.id for t in result.tracks] == [1]

    def test_missing_duration_included_when_requested(self):
        tracks = [
            make_track(1, duration_ms=120_000),
            make_track(2, duration_ms=None),
        ]
        result = filter_by_duration(
            min_ms=60_000, include_missing=True, tracklist=TrackList(tracks=tracks)
        )
        assert [t.id for t in result.tracks] == [1, 2]

    def test_no_constraints_keeps_all(self):
        tracks = [
            make_track(1, duration_ms=30_000),
            make_track(2, duration_ms=600_000),
        ]
        result = filter_by_duration(tracklist=TrackList(tracks=tracks))
        assert len(result.tracks) == 2


class TestFilterByLikedStatus:
    def _liked_track(self, id: int, service: str = "spotify") -> Track:
        track = make_track(id)
        return track.with_connector_metadata(service, {"is_liked": True})

    def _unliked_track(self, id: int) -> Track:
        return make_track(id)

    def test_keep_liked(self):
        tracks = [self._liked_track(1), self._unliked_track(2), self._liked_track(3)]
        result = filter_by_liked_status(
            service="spotify", is_liked=True, tracklist=TrackList(tracks=tracks)
        )
        assert [t.id for t in result.tracks] == [1, 3]

    def test_keep_unloved(self):
        tracks = [self._liked_track(1), self._unliked_track(2), self._liked_track(3)]
        result = filter_by_liked_status(
            service="spotify", is_liked=False, tracklist=TrackList(tracks=tracks)
        )
        assert [t.id for t in result.tracks] == [2]

    def test_service_specific(self):
        """Liked on spotify but not lastfm."""
        track = self._liked_track(1, service="spotify")
        result = filter_by_liked_status(
            service="lastfm", is_liked=True, tracklist=TrackList(tracks=[track])
        )
        assert result.tracks == []


class TestIntersect:
    def test_two_lists_with_overlap(self):
        tl1 = TrackList(tracks=[make_track(1), make_track(2), make_track(3)])
        tl2 = TrackList(tracks=[make_track(2), make_track(3), make_track(4)])

        result = intersect([tl1, tl2], tracklist=TrackList())

        assert [t.id for t in result.tracks] == [2, 3]
        assert result.metadata.get("operation") == "intersect"
        assert result.metadata.get("source_count") == 2

    def test_three_lists(self):
        tl1 = TrackList(tracks=[make_track(1), make_track(2), make_track(3)])
        tl2 = TrackList(tracks=[make_track(2), make_track(3)])
        tl3 = TrackList(tracks=[make_track(3), make_track(4)])

        result = intersect([tl1, tl2, tl3], tracklist=TrackList())
        assert [t.id for t in result.tracks] == [3]

    def test_no_overlap(self):
        tl1 = TrackList(tracks=[make_track(1)])
        tl2 = TrackList(tracks=[make_track(2)])

        result = intersect([tl1, tl2], tracklist=TrackList())
        assert result.tracks == []

    def test_empty_input(self):
        result = intersect([], tracklist=TrackList())
        assert result.tracks == []

    def test_preserves_first_list_order(self):
        tl1 = TrackList(tracks=[make_track(3), make_track(1), make_track(2)])
        tl2 = TrackList(tracks=[make_track(1), make_track(2), make_track(3)])

        result = intersect([tl1, tl2], tracklist=TrackList())
        assert [t.id for t in result.tracks] == [3, 1, 2]

    def test_single_list(self):
        tl = TrackList(tracks=[make_track(1), make_track(2)])
        result = intersect([tl], tracklist=TrackList())
        assert [t.id for t in result.tracks] == [1, 2]


class TestSelectByPercentage:
    def test_basic_percentage(self):
        tracks = [make_track(i) for i in range(1, 11)]  # 10 tracks
        result = select_by_percentage(percentage=50, tracklist=TrackList(tracks=tracks))
        assert len(result.tracks) == 5

    def test_rounds_correctly(self):
        tracks = [make_track(i) for i in range(1, 8)]  # 7 tracks
        result = select_by_percentage(percentage=30, tracklist=TrackList(tracks=tracks))
        # 7 * 0.30 = 2.1 -> rounds to 2
        assert len(result.tracks) == 2

    def test_minimum_one_track(self):
        tracks = [make_track(i) for i in range(1, 101)]  # 100 tracks
        result = select_by_percentage(
            percentage=0.1, tracklist=TrackList(tracks=tracks)
        )
        # 100 * 0.001 = 0.1 -> max(1, round(0.1)) = 1
        assert len(result.tracks) >= 1

    def test_100_percent(self):
        tracks = [make_track(i) for i in range(1, 6)]
        result = select_by_percentage(
            percentage=100, tracklist=TrackList(tracks=tracks)
        )
        assert len(result.tracks) == 5

    def test_method_last(self):
        tracks = [make_track(i) for i in range(1, 11)]
        result = select_by_percentage(
            percentage=30, method="last", tracklist=TrackList(tracks=tracks)
        )
        # 10 * 0.30 = 3 tracks from the end
        assert len(result.tracks) == 3
        assert [t.id for t in result.tracks] == [8, 9, 10]

    def test_small_list(self):
        tracks = [make_track(1), make_track(2)]
        result = select_by_percentage(percentage=50, tracklist=TrackList(tracks=tracks))
        assert len(result.tracks) == 1


def _track_released(track_id: int, year: int | None) -> Track:
    """Track with a UTC release_date at midyear, or no release date when None."""
    release_date = datetime(year, 6, 1, tzinfo=UTC) if year is not None else None
    return make_track(track_id, release_date=release_date)


class TestFilterByReleaseYear:
    """filter_by_release_year — absolute year range, unlike age-in-days drift."""

    def test_in_range_kept_out_of_range_dropped(self):
        tracks = [
            _track_released(1, 2009),  # below
            _track_released(2, 2010),  # lower boundary (inclusive)
            _track_released(3, 2015),  # inside
            _track_released(4, 2019),  # upper boundary (inclusive)
            _track_released(5, 2020),  # above
        ]
        result = filter_by_release_year(
            min_year=2010, max_year=2019, tracklist=TrackList(tracks=tracks)
        )
        assert [t.id for t in result.tracks] == [2, 3, 4]

    def test_boundary_years_are_inclusive(self):
        tracks = [_track_released(1, 2010), _track_released(2, 2019)]
        result = filter_by_release_year(
            min_year=2010, max_year=2019, tracklist=TrackList(tracks=tracks)
        )
        assert len(result.tracks) == 2

    def test_only_min_year(self):
        tracks = [_track_released(1, 2008), _track_released(2, 2012)]
        result = filter_by_release_year(
            min_year=2010, tracklist=TrackList(tracks=tracks)
        )
        assert [t.id for t in result.tracks] == [2]

    def test_only_max_year(self):
        tracks = [_track_released(1, 2008), _track_released(2, 2012)]
        result = filter_by_release_year(
            max_year=2010, tracklist=TrackList(tracks=tracks)
        )
        assert [t.id for t in result.tracks] == [1]

    def test_missing_release_date_excluded_by_default(self):
        tracks = [_track_released(1, 2015), _track_released(2, None)]
        result = filter_by_release_year(
            min_year=2010, max_year=2019, tracklist=TrackList(tracks=tracks)
        )
        assert [t.id for t in result.tracks] == [1]

    def test_missing_release_date_included_when_requested(self):
        tracks = [_track_released(1, 2015), _track_released(2, None)]
        result = filter_by_release_year(
            min_year=2010,
            max_year=2019,
            include_missing=True,
            tracklist=TrackList(tracks=tracks),
        )
        assert {t.id for t in result.tracks} == {1, 2}

    def test_dual_mode_returns_transform_without_tracklist(self):
        transform = filter_by_release_year(min_year=2010)
        assert callable(transform)  # Transform is a Callable alias, not isinstance-able
        tracks = [_track_released(1, 2008), _track_released(2, 2012)]
        result = transform(TrackList(tracks=tracks))
        assert [t.id for t in result.tracks] == [2]
