"""Tests for filter_duplicates deduplication behavior.

Verifies that filter_duplicates correctly deduplicates tracks by UUID.
"""

from uuid import uuid7

from src.domain.entities.track import TrackList
from src.domain.transforms.filtering import filter_duplicates
from tests.fixtures import make_persisted_track


class TestFilterDuplicatesInvariant:
    """filter_duplicates deduplicates tracks by UUID."""

    def test_deduplicates_by_id_keeping_first_occurrence(self):
        shared_id = uuid7()
        other = make_persisted_track()
        tracks = [
            make_persisted_track(id=shared_id),
            other,
            make_persisted_track(id=shared_id),
        ]
        tracklist = TrackList(tracks=tracks)
        result = filter_duplicates(tracklist=tracklist)
        assert [t.id for t in result.tracks] == [shared_id, other.id]
        assert result.tracks[0] is tracks[0]
