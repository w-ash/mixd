"""Tests for filter_duplicates invariant enforcement.

Verifies that filter_duplicates raises TracklistInvariantError when
tracks without database IDs are passed, instead of silently passing them through.
"""

import pytest

from src.domain.entities.track import TrackList
from src.domain.exceptions import TracklistInvariantError
from src.domain.transforms.filtering import filter_duplicates
from tests.fixtures import make_track


class TestFilterDuplicatesInvariant:
    """filter_duplicates now requires all tracks to have database IDs."""

    def test_raises_on_id_none_track(self):
        tracks = [make_track(id=1), make_track(id=None, title="Ghost")]
        tracklist = TrackList(tracks=tracks)
        with pytest.raises(TracklistInvariantError, match="1 tracks lack database IDs"):
            filter_duplicates(tracklist=tracklist)

    def test_deduplicates_by_id(self):
        tracks = [make_track(id=1), make_track(id=2), make_track(id=1)]
        tracklist = TrackList(tracks=tracks)
        result = filter_duplicates(tracklist=tracklist)
        assert [t.id for t in result.tracks] == [1, 2]

    def test_empty_tracklist_passes(self):
        result = filter_duplicates(tracklist=TrackList())
        assert result.tracks == []

    def test_no_duplicates(self):
        tracks = [make_track(id=i) for i in range(1, 4)]
        tracklist = TrackList(tracks=tracks)
        result = filter_duplicates(tracklist=tracklist)
        assert len(result.tracks) == 3
