"""Tests for connector playlist item factory functions.

Tests creation of ConnectorPlaylistItem instances from Track entities,
including single-track and batch operations with correct filtering of
tracks that lack connector identifiers.
"""

from datetime import UTC, datetime

import pytest

from src.application.use_cases._shared.connector_playlist_factories import (
    create_connector_playlist_item_from_track,
    create_connector_playlist_items_from_tracks,
)
from src.domain.entities.playlist import ConnectorPlaylistItem
from src.domain.entities.track import Artist, Track


def _make_track(
    title: str = "Test Track",
    connector_ids: dict[str, str] | None = None,
    track_id: int | None = None,
) -> Track:
    """Helper to create a Track with optional connector identifiers."""
    return Track(
        id=track_id,
        title=title,
        artists=[Artist(name="Test Artist")],
        connector_track_identifiers=connector_ids or {},
    )


@pytest.mark.unit
class TestCreateConnectorPlaylistItemFromTrack:
    """Test single-track ConnectorPlaylistItem creation."""

    def test_track_with_connector_id_returns_item(self):
        """Track with matching connector ID should produce a ConnectorPlaylistItem."""
        track = _make_track(connector_ids={"spotify": "sp_123"})
        fixed_time = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)

        item = create_connector_playlist_item_from_track(
            track=track,
            position=0,
            connector_name="spotify",
            added_at=fixed_time,
        )

        assert item is not None
        assert isinstance(item, ConnectorPlaylistItem)
        assert item.connector_track_identifier == "sp_123"
        assert item.position == 0
        assert item.added_at == fixed_time.isoformat()
        assert item.added_by_id == "narada"
        assert item.extras["track_uri"] == "spotify:track:sp_123"
        assert item.extras["local"] is False

    def test_track_without_connector_identifiers_returns_none(self):
        """Track with empty connector_track_identifiers should return None."""
        track = _make_track(connector_ids={})

        item = create_connector_playlist_item_from_track(
            track=track,
            position=0,
            connector_name="spotify",
        )

        assert item is None

    def test_track_with_wrong_connector_name_returns_none(self):
        """Track with connector IDs for a different service should return None."""
        track = _make_track(connector_ids={"lastfm": "lf_456"})

        item = create_connector_playlist_item_from_track(
            track=track,
            position=0,
            connector_name="spotify",
        )

        assert item is None

    def test_custom_added_by_id(self):
        """Should respect custom added_by_id parameter."""
        track = _make_track(connector_ids={"spotify": "sp_123"})

        item = create_connector_playlist_item_from_track(
            track=track,
            position=0,
            connector_name="spotify",
            added_by_id="user_42",
        )

        assert item is not None
        assert item.added_by_id == "user_42"

    def test_added_at_defaults_to_now_when_none(self):
        """When added_at is None, should default to current UTC datetime."""
        track = _make_track(connector_ids={"spotify": "sp_123"})
        before = datetime.now(UTC)

        item = create_connector_playlist_item_from_track(
            track=track,
            position=0,
            connector_name="spotify",
            added_at=None,
        )

        after = datetime.now(UTC)
        assert item is not None
        item_time = datetime.fromisoformat(item.added_at)
        assert before <= item_time <= after

    def test_position_is_preserved(self):
        """Position parameter should be passed through to the item."""
        track = _make_track(connector_ids={"spotify": "sp_123"})

        item = create_connector_playlist_item_from_track(
            track=track,
            position=42,
            connector_name="spotify",
        )

        assert item is not None
        assert item.position == 42


@pytest.mark.unit
class TestCreateConnectorPlaylistItemsFromTracks:
    """Test batch creation of ConnectorPlaylistItems."""

    def test_batch_creates_items_for_matching_tracks(self):
        """Should create items only for tracks with the specified connector ID."""
        tracks = [
            _make_track(title="Has Spotify", connector_ids={"spotify": "sp_1"}),
            _make_track(title="Has Spotify Too", connector_ids={"spotify": "sp_2"}),
        ]

        items = create_connector_playlist_items_from_tracks(
            tracks=tracks,
            connector_name="spotify",
        )

        assert len(items) == 2
        assert items[0].connector_track_identifier == "sp_1"
        assert items[1].connector_track_identifier == "sp_2"

    def test_batch_filters_tracks_without_connector_ids(self):
        """Should skip tracks that lack the specified connector ID."""
        tracks = [
            _make_track(title="Has Spotify", connector_ids={"spotify": "sp_1"}),
            _make_track(title="No IDs", connector_ids={}),
            _make_track(title="Wrong Service", connector_ids={"lastfm": "lf_1"}),
            _make_track(title="Also Spotify", connector_ids={"spotify": "sp_3"}),
        ]

        items = create_connector_playlist_items_from_tracks(
            tracks=tracks,
            connector_name="spotify",
        )

        assert len(items) == 2
        assert items[0].connector_track_identifier == "sp_1"
        assert items[1].connector_track_identifier == "sp_3"

    def test_positions_are_zero_indexed_from_enumerate(self):
        """Positions should match the original list index (0-indexed from enumerate)."""
        tracks = [
            _make_track(title="Track 0", connector_ids={"spotify": "sp_0"}),
            _make_track(title="Track 1 (no ID)", connector_ids={}),
            _make_track(title="Track 2", connector_ids={"spotify": "sp_2"}),
        ]

        items = create_connector_playlist_items_from_tracks(
            tracks=tracks,
            connector_name="spotify",
        )

        # Position comes from enumerate over the full list, not filtered list
        assert items[0].position == 0
        assert items[1].position == 2

    def test_empty_track_list_returns_empty(self):
        """Empty input should produce empty output."""
        items = create_connector_playlist_items_from_tracks(
            tracks=[],
            connector_name="spotify",
        )

        assert items == []

    def test_all_tracks_filtered_returns_empty(self):
        """When no tracks have the connector ID, should return empty list."""
        tracks = [
            _make_track(title="LastFM Only", connector_ids={"lastfm": "lf_1"}),
            _make_track(title="No IDs", connector_ids={}),
        ]

        items = create_connector_playlist_items_from_tracks(
            tracks=tracks,
            connector_name="spotify",
        )

        assert items == []

    def test_batch_shares_timestamp(self):
        """All items in a batch should share the same added_at timestamp."""
        fixed_time = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        tracks = [
            _make_track(title="A", connector_ids={"spotify": "sp_a"}),
            _make_track(title="B", connector_ids={"spotify": "sp_b"}),
            _make_track(title="C", connector_ids={"spotify": "sp_c"}),
        ]

        items = create_connector_playlist_items_from_tracks(
            tracks=tracks,
            connector_name="spotify",
            added_at=fixed_time,
        )

        expected_iso = fixed_time.isoformat()
        for item in items:
            assert item.added_at == expected_iso
