"""Tests for combiner deduplication option.

Verifies that concatenate, interleave, and intersect correctly deduplicate
when the `deduplicate=True` parameter is passed.
"""

from uuid import uuid7

from src.domain.entities.track import TrackList
from src.domain.transforms.combining import concatenate, interleave, intersect
from tests.fixtures import make_persisted_track


class TestConcatenateDedup:
    def test_no_dedup_preserves_duplicates(self):
        id1, id2, id3 = uuid7(), uuid7(), uuid7()
        tl1 = TrackList(
            tracks=[make_persisted_track(id=id1), make_persisted_track(id=id2)]
        )
        tl2 = TrackList(
            tracks=[make_persisted_track(id=id2), make_persisted_track(id=id3)]
        )
        result = concatenate([tl1, tl2], tracklist=TrackList())
        assert [t.id for t in result.tracks] == [id1, id2, id2, id3]

    def test_dedup_removes_duplicates(self):
        id1, id2, id3 = uuid7(), uuid7(), uuid7()
        tl1 = TrackList(
            tracks=[make_persisted_track(id=id1), make_persisted_track(id=id2)]
        )
        tl2 = TrackList(
            tracks=[make_persisted_track(id=id2), make_persisted_track(id=id3)]
        )
        result = concatenate([tl1, tl2], deduplicate=True, tracklist=TrackList())
        assert [t.id for t in result.tracks] == [id1, id2, id3]

    def test_dedup_false_is_default(self):
        shared_id = uuid7()
        tl1 = TrackList(tracks=[make_persisted_track(id=shared_id)])
        tl2 = TrackList(tracks=[make_persisted_track(id=shared_id)])
        result = concatenate([tl1, tl2], tracklist=TrackList())
        assert len(result.tracks) == 2


class TestInterleaveDedup:
    def test_no_dedup_preserves_duplicates(self):
        id1, id2, id3 = uuid7(), uuid7(), uuid7()
        tl1 = TrackList(
            tracks=[make_persisted_track(id=id1), make_persisted_track(id=id2)]
        )
        tl2 = TrackList(
            tracks=[make_persisted_track(id=id1), make_persisted_track(id=id3)]
        )
        result = interleave([tl1, tl2], tracklist=TrackList())
        # Interleave: tl1[0], tl2[0], tl1[1], tl2[1]
        assert [t.id for t in result.tracks] == [id1, id1, id2, id3]

    def test_dedup_removes_duplicates(self):
        id1, id2, id3 = uuid7(), uuid7(), uuid7()
        tl1 = TrackList(
            tracks=[make_persisted_track(id=id1), make_persisted_track(id=id2)]
        )
        tl2 = TrackList(
            tracks=[make_persisted_track(id=id1), make_persisted_track(id=id3)]
        )
        result = interleave([tl1, tl2], deduplicate=True, tracklist=TrackList())
        assert [t.id for t in result.tracks] == [id1, id2, id3]


class TestIntersectDedup:
    def test_intersect_with_dedup(self):
        """Intersect already produces unique results, but deduplicate shouldn't break it."""
        id1, id2, id3 = uuid7(), uuid7(), uuid7()
        tl1 = TrackList(
            tracks=[make_persisted_track(id=id1), make_persisted_track(id=id2)]
        )
        tl2 = TrackList(
            tracks=[make_persisted_track(id=id2), make_persisted_track(id=id3)]
        )
        result = intersect([tl1, tl2], deduplicate=True, tracklist=TrackList())
        assert [t.id for t in result.tracks] == [id2]
