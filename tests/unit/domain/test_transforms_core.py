"""Tests for domain transform core utilities: require_database_tracks."""

import pytest

from src.domain.entities.track import TrackList
from src.domain.exceptions import TracklistInvariantError
from src.domain.transforms.core import require_database_tracks
from tests.fixtures import make_track


class TestRequireDatabaseTracks:
    """Tests for the require_database_tracks invariant check."""

    def test_passes_when_all_tracks_have_ids(self):
        tracks = [make_track(id=1), make_track(id=2), make_track(id=3)]
        tracklist = TrackList(tracks=tracks)
        # Should not raise
        require_database_tracks(tracklist)

    def test_passes_on_empty_tracklist(self):
        tracklist = TrackList(tracks=[])
        # Should not raise — empty tracklist is valid
        require_database_tracks(tracklist)

    def test_raises_when_track_has_no_id(self):
        tracks = [make_track(id=1), make_track(id=None, title="Orphan")]
        tracklist = TrackList(tracks=tracks)
        with pytest.raises(TracklistInvariantError, match="1 tracks lack database IDs"):
            require_database_tracks(tracklist)

    def test_raises_with_multiple_id_none_tracks(self):
        tracks = [
            make_track(id=None, title="A"),
            make_track(id=None, title="B"),
            make_track(id=None, title="C"),
        ]
        tracklist = TrackList(tracks=tracks)
        with pytest.raises(TracklistInvariantError, match="3 tracks lack database IDs"):
            require_database_tracks(tracklist)

    def test_error_message_includes_track_titles(self):
        tracks = [make_track(id=None, title="Lost Song")]
        tracklist = TrackList(tracks=tracks)
        with pytest.raises(TracklistInvariantError, match="Lost Song"):
            require_database_tracks(tracklist)

    def test_error_message_truncates_after_five(self):
        tracks = [make_track(id=None, title=f"Song {i}") for i in range(8)]
        tracklist = TrackList(tracks=tracks)
        with pytest.raises(
            TracklistInvariantError, match="8 tracks lack database IDs"
        ) as exc_info:
            require_database_tracks(tracklist)
        # Only first 5 titles shown
        msg = str(exc_info.value)
        assert "Song 0" in msg
        assert "Song 4" in msg
        assert "Song 5" not in msg
