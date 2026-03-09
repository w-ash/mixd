"""Tests for domain transform core utilities: require_database_tracks and quarantine_invalid_tracks."""

import pytest

from src.domain.entities.track import TrackList
from src.domain.exceptions import TracklistInvariantError
from src.domain.transforms.core import quarantine_invalid_tracks, require_database_tracks
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
        with pytest.raises(TracklistInvariantError, match="8 tracks lack database IDs") as exc_info:
            require_database_tracks(tracklist)
        # Only first 5 titles shown
        msg = str(exc_info.value)
        assert "Song 0" in msg
        assert "Song 4" in msg
        assert "Song 5" not in msg


class TestQuarantineInvalidTracks:
    """Tests for the quarantine_invalid_tracks graceful degradation function."""

    def test_returns_all_tracks_when_all_valid(self):
        tracks = [make_track(id=1), make_track(id=2)]
        tracklist = TrackList(tracks=tracks)
        valid, quarantined = quarantine_invalid_tracks(tracklist)
        assert len(valid.tracks) == 2
        assert quarantined == []

    def test_returns_empty_quarantined_for_empty_tracklist(self):
        tracklist = TrackList(tracks=[])
        valid, quarantined = quarantine_invalid_tracks(tracklist)
        assert len(valid.tracks) == 0
        assert quarantined == []

    def test_separates_valid_from_invalid(self):
        tracks = [
            make_track(id=1, title="Good"),
            make_track(id=None, title="Bad"),
            make_track(id=2, title="Also Good"),
        ]
        tracklist = TrackList(tracks=tracks)
        valid, quarantined = quarantine_invalid_tracks(tracklist)
        assert len(valid.tracks) == 2
        assert {t.title for t in valid.tracks} == {"Good", "Also Good"}
        assert len(quarantined) == 1
        assert quarantined[0].title == "Bad"

    def test_raises_when_all_tracks_invalid(self):
        tracks = [make_track(id=None, title="A"), make_track(id=None, title="B")]
        tracklist = TrackList(tracks=tracks)
        with pytest.raises(TracklistInvariantError, match="All 2 tracks lack database IDs"):
            quarantine_invalid_tracks(tracklist)

    def test_raises_single_invalid_track_when_no_valid(self):
        tracks = [make_track(id=None, title="Only One")]
        tracklist = TrackList(tracks=tracks)
        with pytest.raises(TracklistInvariantError, match="All 1 tracks"):
            quarantine_invalid_tracks(tracklist)
