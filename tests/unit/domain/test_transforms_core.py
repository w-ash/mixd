"""Tests for domain transform core utilities: require_database_tracks."""

from src.domain.entities.track import TrackList
from src.domain.transforms.core import require_database_tracks
from tests.fixtures import make_track


class TestRequireDatabaseTracks:
    """Tests for the require_database_tracks guard (now a no-op with UUIDv7)."""

    def test_no_op_with_tracks(self):
        """All tracks have UUIDs, so require_database_tracks is a no-op."""
        tracks = [make_track(), make_track(), make_track()]
        tracklist = TrackList(tracks=tracks)
        # Should not raise — no-op
        require_database_tracks(tracklist)

    def test_no_op_on_empty_tracklist(self):
        tracklist = TrackList(tracks=[])
        # Should not raise — empty tracklist is valid
        require_database_tracks(tracklist)
