"""Unit tests for PlaylistLink defaults and the persisted sync enum values."""

from datetime import UTC, datetime
from uuid import uuid7

from src.domain.entities.playlist_link import PlaylistLink, SyncDirection, SyncStatus


def _new_link() -> PlaylistLink:
    return PlaylistLink(
        playlist_id=uuid7(),
        connector_name="spotify",
        connector_playlist_identifier="abc123",
    )


class TestSyncDirection:
    """SyncDirection values are stored in the link row and sent over the API."""

    def test_push_value(self):
        assert SyncDirection.PUSH.value == "push"

    def test_pull_value(self):
        assert SyncDirection.PULL.value == "pull"


class TestSyncStatus:
    """SyncStatus values are stored in the link row and sent over the API."""

    def test_all_values(self):
        assert SyncStatus.NEVER_SYNCED.value == "never_synced"
        assert SyncStatus.SYNCED.value == "synced"
        assert SyncStatus.SYNCING.value == "syncing"
        assert SyncStatus.ERROR.value == "error"


class TestPlaylistLink:
    def test_new_link_pulls_and_has_never_synced(self):
        # Default PULL (v0.8.7): a freshly linked playlist keeps pulling from the
        # connector rather than overwriting it with a near-empty canonical.
        link = _new_link()
        assert link.sync_direction == SyncDirection.PULL
        assert link.sync_status == SyncStatus.NEVER_SYNCED

    def test_created_at_is_stamped_now_in_utc(self):
        before = datetime.now(UTC)
        link = _new_link()
        after = datetime.now(UTC)

        assert before <= link.created_at <= after
        assert link.created_at.tzinfo == UTC
