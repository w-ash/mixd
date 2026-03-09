"""Tests for combiner deduplication option.

Verifies that concatenate, interleave, and intersect correctly deduplicate
when the `deduplicate=True` parameter is passed.
"""

from src.domain.entities.track import TrackList
from src.domain.transforms.combining import concatenate, interleave, intersect
from tests.fixtures import make_track


class TestConcatenateDedup:
    def test_no_dedup_preserves_duplicates(self):
        tl1 = TrackList(tracks=[make_track(id=1), make_track(id=2)])
        tl2 = TrackList(tracks=[make_track(id=2), make_track(id=3)])
        result = concatenate([tl1, tl2], tracklist=TrackList())
        assert [t.id for t in result.tracks] == [1, 2, 2, 3]

    def test_dedup_removes_duplicates(self):
        tl1 = TrackList(tracks=[make_track(id=1), make_track(id=2)])
        tl2 = TrackList(tracks=[make_track(id=2), make_track(id=3)])
        result = concatenate([tl1, tl2], deduplicate=True, tracklist=TrackList())
        assert [t.id for t in result.tracks] == [1, 2, 3]

    def test_dedup_false_is_default(self):
        tl1 = TrackList(tracks=[make_track(id=1)])
        tl2 = TrackList(tracks=[make_track(id=1)])
        result = concatenate([tl1, tl2], tracklist=TrackList())
        assert len(result.tracks) == 2


class TestInterleaveDedup:
    def test_no_dedup_preserves_duplicates(self):
        tl1 = TrackList(tracks=[make_track(id=1), make_track(id=2)])
        tl2 = TrackList(tracks=[make_track(id=1), make_track(id=3)])
        result = interleave([tl1, tl2], tracklist=TrackList())
        # Interleave: tl1[0], tl2[0], tl1[1], tl2[1] = 1, 1, 2, 3
        assert [t.id for t in result.tracks] == [1, 1, 2, 3]

    def test_dedup_removes_duplicates(self):
        tl1 = TrackList(tracks=[make_track(id=1), make_track(id=2)])
        tl2 = TrackList(tracks=[make_track(id=1), make_track(id=3)])
        result = interleave([tl1, tl2], deduplicate=True, tracklist=TrackList())
        assert [t.id for t in result.tracks] == [1, 2, 3]


class TestIntersectDedup:
    def test_intersect_with_dedup(self):
        """Intersect already produces unique results, but deduplicate shouldn't break it."""
        tl1 = TrackList(tracks=[make_track(id=1), make_track(id=2)])
        tl2 = TrackList(tracks=[make_track(id=2), make_track(id=3)])
        result = intersect([tl1, tl2], deduplicate=True, tracklist=TrackList())
        assert [t.id for t in result.tracks] == [2]
