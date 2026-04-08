"""Tests for domain transform core utilities: require_database_tracks, dual_mode."""

from collections.abc import Callable

from src.domain.entities.track import TrackList
from src.domain.transforms.core import dual_mode, require_database_tracks
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


class TestDualMode:
    """Tests for the dual_mode helper used by all transform factories."""

    @staticmethod
    def _identity_transform(t: TrackList) -> TrackList:
        return t

    def test_returns_transform_when_tracklist_is_none(self):
        result = dual_mode(self._identity_transform, None)
        assert isinstance(result, Callable)
        assert result is self._identity_transform

    def test_returns_tracklist_when_tracklist_provided(self):
        tracklist = TrackList(tracks=[make_track()])
        result = dual_mode(self._identity_transform, tracklist)
        assert isinstance(result, TrackList)
        assert result is tracklist

    def test_applies_transform_to_provided_tracklist(self):
        def reverse_transform(t: TrackList) -> TrackList:
            return t.with_tracks(list(reversed(t.tracks)))

        tracks = [make_track(title="A"), make_track(title="B")]
        tracklist = TrackList(tracks=tracks)
        result = dual_mode(reverse_transform, tracklist)
        assert isinstance(result, TrackList)
        assert result.tracks[0].title == "B"
        assert result.tracks[1].title == "A"

    def test_returns_empty_tracklist_for_empty_input(self):
        result = dual_mode(self._identity_transform, TrackList())
        assert isinstance(result, TrackList)
        assert len(result.tracks) == 0
