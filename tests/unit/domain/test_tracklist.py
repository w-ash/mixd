"""Tests for TrackList behavior.

Covers the copy-on-write helpers (with_tracks, with_metadata) and the
Playlist ↔ TrackList conversion bridge.
"""

from datetime import UTC, datetime

from src.domain.entities.playlist import Playlist, PlaylistEntry
from src.domain.entities.track import TrackList
from tests.fixtures import TEST_USER_ID, make_tracks


class TestTrackListImmutability:
    """Verify frozen semantics — all mutations return new instances."""

    def test_with_tracks_returns_new_instance(self):
        original = TrackList(tracks=make_tracks(2))
        new_tracks = make_tracks(1)

        result = original.with_tracks(new_tracks)

        assert result is not original
        assert result.tracks == new_tracks
        assert len(original.tracks) == 2  # unchanged

    def test_with_tracks_preserves_metadata(self):
        original = TrackList(tracks=make_tracks(2), metadata={"source": "test"})
        result = original.with_tracks([])

        assert result.metadata == {"source": "test"}

    def test_with_metadata_returns_new_instance(self):
        original = TrackList(tracks=[])

        result = original.with_metadata("key", "value")

        assert result is not original
        assert result.metadata["key"] == "value"
        assert original.metadata == {}  # unchanged

    def test_chained_with_metadata_accumulates(self):
        tl = TrackList(tracks=[])
        tl = tl.with_metadata("a", 1).with_metadata("b", 2)

        assert tl.metadata == {"a": 1, "b": 2}


class TestPlaylistTrackListConversion:
    """Verify the Playlist ↔ TrackList bridge."""

    def test_playlist_tracks_property_extracts_tracks(self):
        """playlist.tracks extracts resolved tracks from entries."""
        tracks = make_tracks(3)
        entries = [PlaylistEntry(track=t) for t in tracks]
        playlist = Playlist(name="Test", entries=entries, user_id=TEST_USER_ID)

        assert len(playlist.tracks) == 3
        assert playlist.tracks == tracks

    def test_from_tracklist_creates_playlist_with_entries(self):
        tracks = make_tracks(2)
        tl = TrackList(tracks=tracks)
        now = datetime.now(UTC)

        playlist = Playlist.from_tracklist(
            "New Playlist", tl, added_at=now, user_id=TEST_USER_ID
        )

        assert playlist.name == "New Playlist"
        assert len(playlist.entries) == 2
        assert all(e.added_at == now for e in playlist.entries)
        assert playlist.tracks == tracks

    def test_from_tracklist_accepts_raw_track_list(self):
        """from_tracklist also accepts list[Track] for convenience."""
        tracks = make_tracks(2)

        playlist = Playlist.from_tracklist("Test", tracks, user_id=TEST_USER_ID)

        assert playlist.tracks == tracks
