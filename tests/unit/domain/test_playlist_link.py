"""Unit tests for PlaylistLink entity, SyncDirection, and SyncStatus enums."""

from datetime import UTC, datetime

from src.domain.entities.playlist_link import PlaylistLink, SyncDirection, SyncStatus


class TestSyncDirection:
    """SyncDirection enum values."""

    def test_push_value(self):
        assert SyncDirection.PUSH.value == "push"

    def test_pull_value(self):
        assert SyncDirection.PULL.value == "pull"

    def test_from_string(self):
        assert SyncDirection("push") == SyncDirection.PUSH
        assert SyncDirection("pull") == SyncDirection.PULL


class TestSyncStatus:
    """SyncStatus enum values."""

    def test_all_values(self):
        assert SyncStatus.NEVER_SYNCED.value == "never_synced"
        assert SyncStatus.SYNCED.value == "synced"
        assert SyncStatus.SYNCING.value == "syncing"
        assert SyncStatus.ERROR.value == "error"

    def test_from_string(self):
        assert SyncStatus("synced") == SyncStatus.SYNCED
        assert SyncStatus("error") == SyncStatus.ERROR


class TestPlaylistLink:
    """PlaylistLink entity creation and defaults."""

    def test_minimal_creation(self):
        link = PlaylistLink(
            playlist_id=1,
            connector_name="spotify",
            connector_playlist_identifier="abc123",
        )
        assert link.playlist_id == 1
        assert link.connector_name == "spotify"
        assert link.connector_playlist_identifier == "abc123"
        assert link.sync_direction == SyncDirection.PUSH
        assert link.sync_status == SyncStatus.NEVER_SYNCED
        assert link.last_synced is None
        assert link.last_sync_error is None
        assert link.last_sync_tracks_added is None
        assert link.last_sync_tracks_removed is None
        assert link.id is None

    def test_full_creation(self):
        now = datetime.now(UTC)
        link = PlaylistLink(
            playlist_id=42,
            connector_name="spotify",
            connector_playlist_identifier="37i9dQZF1DZ06evO05tE88",
            connector_playlist_name="My Playlist",
            sync_direction=SyncDirection.PULL,
            sync_status=SyncStatus.SYNCED,
            last_synced=now,
            last_sync_error=None,
            last_sync_tracks_added=5,
            last_sync_tracks_removed=2,
            id=99,
        )
        assert link.playlist_id == 42
        assert link.connector_playlist_name == "My Playlist"
        assert link.sync_direction == SyncDirection.PULL
        assert link.sync_status == SyncStatus.SYNCED
        assert link.last_synced == now
        assert link.last_sync_tracks_added == 5
        assert link.last_sync_tracks_removed == 2
        assert link.id == 99

    def test_created_at_auto_populated(self):
        link = PlaylistLink(
            playlist_id=1,
            connector_name="spotify",
            connector_playlist_identifier="abc",
        )
        assert link.created_at is not None
        assert link.created_at.tzinfo is not None
