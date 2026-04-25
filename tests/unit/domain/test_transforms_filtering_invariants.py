"""Tests for filter_duplicates deduplication behavior.

Verifies that filter_duplicates correctly deduplicates tracks by UUID.
"""

from uuid import uuid7

from src.domain.entities.track import TrackList
from src.domain.transforms.filtering import filter_duplicates
from tests.fixtures import make_persisted_track


class TestFilterDuplicatesInvariant:
    """filter_duplicates deduplicates tracks by UUID."""

    def test_deduplicates_by_id(self):
        shared_id = uuid7()
        tracks = [
            make_persisted_track(id=shared_id),
            make_persisted_track(),
            make_persisted_track(id=shared_id),
        ]
        tracklist = TrackList(tracks=tracks)
        result = filter_duplicates(tracklist=tracklist)
        assert len(result.tracks) == 2
        assert result.tracks[0].id == shared_id

    def test_empty_tracklist_passes(self):
        result = filter_duplicates(tracklist=TrackList())
        assert result.tracks == []

    def test_no_duplicates(self):
        tracks = [make_persisted_track() for _ in range(3)]
        tracklist = TrackList(tracks=tracks)
        result = filter_duplicates(tracklist=tracklist)
        assert len(result.tracks) == 3
