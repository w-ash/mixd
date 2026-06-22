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


class TestMembershipKeys:
    """``membership_keys`` is the complete no-op test for an overwrite/re-pull."""

    def test_resolved_keys_on_track_id_unresolved_on_ref(self):
        playlist = _mixed_playlist()
        keys = playlist.membership_keys
        assert keys[0] == playlist.entries[0].track.id
        assert keys[1] == ("spotify", "x1")
        assert keys[2] == playlist.entries[2].track.id

    def test_identical_membership_compares_equal(self):
        # Same identities + order ⇒ equal keys (a true no-op) even though the
        # entries are distinct objects with their own auto-generated ids.
        a, b = make_track(title="A"), make_track(title="B")
        ref = ConnectorTrackRef("spotify", "x1")

        def build() -> Playlist:
            return Playlist(
                name="Mixed",
                entries=[
                    PlaylistEntry(track=a),
                    PlaylistEntry(track=None, connector_track_ref=ref),
                    PlaylistEntry(track=b),
                ],
            )

        assert build().membership_keys == build().membership_keys

    def test_unresolved_only_change_is_detected(self):
        # The gap a resolved-track diff misses: only the unresolved position
        # differs, yet the playlists are NOT a no-op.
        base = _mixed_playlist()
        without_ghost = base.with_entries([base.entries[0], base.entries[2]])
        assert base.membership_keys != without_ghost.membership_keys


class TestReconcileEntriesFrom:
    """Overwrite/re-pull keeps every source position and preserves identity."""

    def test_unresolved_positions_are_preserved(self):
        # The headline fix: an overwrite from a playlist carrying an unresolved
        # position must keep that position (was silently dropped before).
        current = Playlist(
            name="P", entries=[PlaylistEntry(track=make_track(title="A"))]
        )
        processed = _mixed_playlist()
        reconciled = current.reconcile_entries_from(processed)
        assert [e.is_resolved for e in reconciled] == [True, False, True]
        assert reconciled[1].connector_track_ref.connector_track_identifier == "x1"

    def test_existing_track_keeps_its_membership_identity(self):
        a = make_track(title="A")
        kept = PlaylistEntry(track=a)  # the existing membership (id + added_at)
        current = Playlist(name="P", entries=[kept])
        # Processed re-supplies track A as a fresh entry plus a new track B.
        processed = Playlist(
            name="P",
            entries=[
                PlaylistEntry(track=a),
                PlaylistEntry(track=make_track(title="B")),
            ],
        )
        reconciled = current.reconcile_entries_from(processed)
        # Track A reuses the existing entry (same id); B is new.
        assert reconciled[0].id == kept.id
        assert reconciled[1].track.title == "B"

    def test_order_follows_processed(self):
        a, b = make_track(title="A"), make_track(title="B")
        current = Playlist(
            name="P", entries=[PlaylistEntry(track=a), PlaylistEntry(track=b)]
        )
        processed = Playlist(
            name="P", entries=[PlaylistEntry(track=b), PlaylistEntry(track=a)]
        )
        reconciled = current.reconcile_entries_from(processed)
        assert [e.track.title for e in reconciled] == ["B", "A"]

    def test_empty_current_returns_processed_entries(self):
        # The create-like case: nothing to preserve, so use processed as-is.
        processed = _mixed_playlist()
        reconciled = Playlist(name="P").reconcile_entries_from(processed)
        assert reconciled == processed.entries
