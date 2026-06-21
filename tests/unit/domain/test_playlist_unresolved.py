"""Unit tests for unresolved playlist entries on the domain Playlist/PlaylistEntry.

Proves the "always complete" invariant at the entity level: unresolved entries
keep their position in ``entries``/``track_count`` while being excluded from the
resolved-only ``tracks``/``to_tracklist`` views that diff and workflows consume.
"""

from src.domain.entities.playlist import (
    ConnectorTrackRef,
    Playlist,
    PlaylistEntry,
)
from tests.fixtures import make_track


def _mixed_playlist() -> Playlist:
    a, b = make_track(title="A"), make_track(title="B")
    return Playlist(
        name="Mixed",
        entries=[
            PlaylistEntry(track=a),
            PlaylistEntry(
                track=None,
                connector_track_ref=ConnectorTrackRef(
                    "spotify", "x1", title="Ghost", artists=("Nobody",)
                ),
            ),
            PlaylistEntry(track=b),
        ],
    )


class TestPlaylistEntryResolution:
    def test_resolved_entry_is_resolved(self):
        entry = PlaylistEntry(track=make_track(title="A"))
        assert entry.is_resolved is True
        assert entry.display_title == "A"

    def test_unresolved_entry_uses_ref_title(self):
        entry = PlaylistEntry(
            track=None,
            connector_track_ref=ConnectorTrackRef("spotify", "x1", title="Ghost"),
        )
        assert entry.is_resolved is False
        assert entry.display_title == "Ghost"

    def test_unresolved_entry_without_title_falls_back(self):
        entry = PlaylistEntry(
            track=None,
            connector_track_ref=ConnectorTrackRef("spotify", "x1"),
        )
        assert entry.display_title == "Unknown track"


class TestPlaylistResolvedViews:
    def test_tracks_excludes_unresolved(self):
        playlist = _mixed_playlist()
        # 3 positions, 2 resolved.
        assert len(playlist.entries) == 3
        assert len(playlist.tracks) == 2
        assert all(t is not None for t in playlist.tracks)

    def test_resolved_and_unresolved_accessors(self):
        playlist = _mixed_playlist()
        assert len(playlist.resolved_entries) == 2
        assert len(playlist.unresolved_entries) == 1
        assert playlist.unresolved_count == 1

    def test_to_tracklist_skips_unresolved(self):
        playlist = _mixed_playlist()
        tracklist = playlist.to_tracklist()
        assert len(tracklist.tracks) == 2
