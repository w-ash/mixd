"""Characterization tests for TrackList behavior.

These tests lock down the current TrackList contract before refactoring:
- Immutability guarantees (frozen attrs)
- Metadata flow (write via with_metadata, read via .metadata)
- Playlist ↔ TrackList conversion bridge
"""

from datetime import UTC, datetime

from src.domain.entities.playlist import Playlist, PlaylistEntry
from src.domain.entities.track import Artist, Track, TrackList
from tests.fixtures import make_tracks


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

    def test_with_metadata_does_not_mutate_original_dict(self):
        """Ensure metadata dict is copied, not shared."""
        original = TrackList(tracks=[], metadata={"existing": True})
        result = original.with_metadata("new", True)

        assert "new" not in original.metadata
        assert "existing" in result.metadata


class TestTrackListMetadataRoundTrip:
    """Verify the metrics metadata pattern used by enrichers and transforms."""

    def test_nested_metrics_round_trip(self):
        """Enrichers write metrics as nested dict: metrics[metric_name][track_id] = value."""
        metrics = {
            "lastfm_user_playcount": {1: 100, 2: 50},
            "explicit_flag": {1: True, 2: False},
        }
        tl = TrackList(tracks=make_tracks(2), metadata={"metrics": metrics})

        assert tl.metadata["metrics"]["lastfm_user_playcount"][1] == 100
        assert tl.metadata["metrics"]["explicit_flag"][2] is False

    def test_metrics_via_with_metadata(self):
        """Enrichers use with_metadata("metrics", {...}) to attach metrics."""
        tl = TrackList(tracks=make_tracks(2))
        metrics = {"total_plays": {1: 10, 2: 20}}

        enriched = tl.with_metadata("metrics", metrics)

        assert enriched.metadata["metrics"]["total_plays"][1] == 10
        assert tl.metadata == {}  # original untouched

    def test_fresh_metric_ids_pattern(self):
        """Enrichers also write fresh_metric_ids alongside metrics."""
        tl = TrackList(tracks=make_tracks(2))
        tl = tl.with_metadata("metrics", {"lastfm_user_playcount": {1: 100}})
        tl = tl.with_metadata("fresh_metric_ids", {"lastfm_user_playcount": [1]})

        assert tl.metadata["fresh_metric_ids"]["lastfm_user_playcount"] == [1]


class TestPlaylistTrackListConversion:
    """Verify the Playlist ↔ TrackList bridge."""

    def test_to_tracklist_extracts_tracks(self):
        tracks = make_tracks(3)
        entries = [PlaylistEntry(track=t) for t in tracks]
        playlist = Playlist(name="Test", entries=entries)

        tl = playlist.to_tracklist()

        assert len(tl.tracks) == 3
        assert tl.tracks == tracks

    def test_to_tracklist_returns_source_metadata(self):
        """to_tracklist() carries source_playlist_name and empty added_at_dates."""
        playlist = Playlist(name="Test", entries=[])

        tl = playlist.to_tracklist()

        assert tl.metadata["source_playlist_name"] == "Test"
        assert tl.metadata["added_at_dates"] == {}

    def test_from_tracklist_creates_playlist_with_entries(self):
        tracks = make_tracks(2)
        tl = TrackList(tracks=tracks)
        now = datetime.now(UTC)

        playlist = Playlist.from_tracklist("New Playlist", tl, added_at=now)

        assert playlist.name == "New Playlist"
        assert len(playlist.entries) == 2
        assert all(e.added_at == now for e in playlist.entries)
        assert playlist.tracks == tracks

    def test_from_tracklist_accepts_raw_track_list(self):
        """from_tracklist also accepts list[Track] for convenience."""
        tracks = make_tracks(2)

        playlist = Playlist.from_tracklist("Test", tracks)

        assert len(playlist.entries) == 2

    def test_to_tracklist_preserves_added_at_dates(self):
        """to_tracklist should carry added_at temporal data in metadata."""
        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        tracks = make_tracks(2)
        entries = [PlaylistEntry(track=t, added_at=now) for t in tracks]
        playlist = Playlist(name="Temporal", entries=entries)

        tl = playlist.to_tracklist()

        assert "added_at_dates" in tl.metadata
        assert tl.metadata["added_at_dates"][tracks[0].id] == now.isoformat()
        assert tl.metadata["added_at_dates"][tracks[1].id] == now.isoformat()

    def test_to_tracklist_carries_source_playlist_name(self):
        """to_tracklist should include source_playlist_name in metadata."""
        playlist = Playlist(name="My Playlist", entries=[])

        tl = playlist.to_tracklist()

        assert tl.metadata["source_playlist_name"] == "My Playlist"

    def test_to_tracklist_skips_entries_without_added_at(self):
        """Entries without added_at should not appear in added_at_dates."""
        tracks = make_tracks(2)
        entries = [
            PlaylistEntry(track=tracks[0], added_at=datetime(2025, 1, 1, tzinfo=UTC)),
            PlaylistEntry(track=tracks[1]),  # No added_at
        ]
        playlist = Playlist(name="Mixed", entries=entries)

        tl = playlist.to_tracklist()

        assert tracks[0].id in tl.metadata["added_at_dates"]
        assert tracks[1].id not in tl.metadata["added_at_dates"]

    def test_to_tracklist_all_tracks_have_ids(self):
        """All tracks have UUIDs, so all entries with added_at should appear in added_at_dates."""
        now = datetime(2025, 1, 1, tzinfo=UTC)
        track_a = Track(title="Track A", artists=[Artist(name="A")])
        track_b = Track(title="Track B", artists=[Artist(name="B")])
        entries = [
            PlaylistEntry(track=track_a, added_at=now),
            PlaylistEntry(track=track_b, added_at=now),
        ]
        playlist = Playlist(name="Mixed", entries=entries)

        tl = playlist.to_tracklist()

        assert track_a.id in tl.metadata["added_at_dates"]
        assert track_b.id in tl.metadata["added_at_dates"]
        assert len(tl.metadata["added_at_dates"]) == 2
