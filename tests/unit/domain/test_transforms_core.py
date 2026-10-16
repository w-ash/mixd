"""Tests for domain transform core utilities: require_database_tracks, dual_mode."""

import pytest

from src.domain.entities.track import TrackList
from src.domain.exceptions import TracklistInvariantError
from src.domain.transforms.core import dual_mode, require_database_tracks
from tests.fixtures import make_persisted_track, make_track


class TestRequireDatabaseTracks:
    """The guard rejects a pipeline carrying tracks that were never persisted."""

    def test_unpersisted_tracks_raise_and_are_named(self):
        """A version-0 track means an upstream source failed to persist it."""
        tracklist = TrackList(
            tracks=[
                make_persisted_track(title="Saved"),
                make_track(title="Lost"),  # version=0
                make_persisted_track(title="Also Saved"),
            ]
        )

        with pytest.raises(TracklistInvariantError, match=r"^1 tracks .*\['Lost'\]$"):
            require_database_tracks(tracklist)


class TestDualMode:
    """Tests for the dual_mode helper used by all transform factories."""

    @staticmethod
    def _identity_transform(t: TrackList) -> TrackList:
        return t

    def test_returns_transform_when_tracklist_is_none(self):
        result = dual_mode(self._identity_transform, None)
        assert result is self._identity_transform

    def test_applies_transform_to_provided_tracklist(self):
        def reverse_transform(t: TrackList) -> TrackList:
            return t.with_tracks(list(reversed(t.tracks)))

        tracks = [make_track(title="A"), make_track(title="B")]
        tracklist = TrackList(tracks=tracks)
        result = dual_mode(reverse_transform, tracklist)
        assert isinstance(result, TrackList)
        assert result.tracks[0].title == "B"
        assert result.tracks[1].title == "A"
