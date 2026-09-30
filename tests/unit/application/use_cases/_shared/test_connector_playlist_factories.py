"""Tests for connector playlist item factory functions.

Tests creation of ConnectorPlaylistItem instances from Track entities,
including single-track and batch operations with correct filtering of
tracks that lack connector identifiers.
"""

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from src.application.use_cases._shared import connector_playlist_factories
from src.application.use_cases._shared.connector_playlist_factories import (
    create_connector_playlist_item_from_track,
    create_connector_playlist_items_from_tracks,
)
from tests.fixtures.factories import make_track

_T0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
_T1 = datetime(2025, 6, 15, 12, 0, 1, tzinfo=UTC)
_T2 = datetime(2025, 6, 15, 12, 0, 2, tzinfo=UTC)


@pytest.fixture
def ticking_clock(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Replace the module clock: each ``now()`` call returns the next second."""
    clock = Mock(now=Mock(side_effect=[_T0, _T1, _T2]))
    monkeypatch.setattr(connector_playlist_factories, "datetime", clock)
    return clock


class TestCreateConnectorPlaylistItemFromTrack:
    """Test single-track ConnectorPlaylistItem creation."""

    def test_track_with_connector_id_returns_item(self):
        """Track with matching connector ID should produce a ConnectorPlaylistItem."""
        track = make_track(connector_track_identifiers={"spotify": "sp_123"})
        fixed_time = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)

        item = create_connector_playlist_item_from_track(
            track=track,
            position=0,
            connector_name="spotify",
            added_at=fixed_time,
        )

        assert item is not None
        assert item.connector_track_identifier == "sp_123"
        assert item.position == 0
        assert item.added_at == fixed_time.isoformat()
        assert item.added_by_id == "mixd"
        assert item.extras["track_uri"] == "spotify:track:sp_123"
        assert item.extras["local"] is False

    @pytest.mark.parametrize(
        "identifiers", [{}, {"lastfm": "lf_456"}], ids=["no_ids", "other_service"]
    )
    def test_track_without_this_connectors_id_returns_none(self, identifiers):
        """No ID for the requested connector -> no playlist item."""
        track = make_track(connector_track_identifiers=identifiers)

        item = create_connector_playlist_item_from_track(
            track=track,
            position=0,
            connector_name="spotify",
        )

        assert item is None

    def test_custom_added_by_id(self):
        """Should respect custom added_by_id parameter."""
        track = make_track(connector_track_identifiers={"spotify": "sp_123"})

        item = create_connector_playlist_item_from_track(
            track=track,
            position=0,
            connector_name="spotify",
            added_by_id="user_42",
        )

        assert item is not None
        assert item.added_by_id == "user_42"

    def test_added_at_defaults_to_now_when_none(self, ticking_clock: Mock):
        """When added_at is None, the item is stamped with the current UTC time."""
        track = make_track(connector_track_identifiers={"spotify": "sp_123"})

        item = create_connector_playlist_item_from_track(
            track=track,
            position=0,
            connector_name="spotify",
            added_at=None,
        )

        assert item is not None
        assert item.added_at == "2025-06-15T12:00:00+00:00"
        ticking_clock.now.assert_called_once_with(UTC)


class TestCreateConnectorPlaylistItemsFromTracks:
    """Test batch creation of ConnectorPlaylistItems."""

    def test_batch_filters_tracks_without_connector_ids(self):
        """Should skip tracks that lack the specified connector ID."""
        tracks = [
            make_track(
                title="Has Spotify", connector_track_identifiers={"spotify": "sp_1"}
            ),
            make_track(title="No IDs", connector_track_identifiers={}),
            make_track(
                title="Wrong Service", connector_track_identifiers={"lastfm": "lf_1"}
            ),
            make_track(
                title="Also Spotify", connector_track_identifiers={"spotify": "sp_3"}
            ),
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
            make_track(
                title="Track 0", connector_track_identifiers={"spotify": "sp_0"}
            ),
            make_track(title="Track 1 (no ID)", connector_track_identifiers={}),
            make_track(
                title="Track 2", connector_track_identifiers={"spotify": "sp_2"}
            ),
        ]

        items = create_connector_playlist_items_from_tracks(
            tracks=tracks,
            connector_name="spotify",
        )

        # Position comes from enumerate over the full list, not filtered list
        assert items[0].position == 0
        assert items[1].position == 2

    def test_all_tracks_filtered_returns_empty(self):
        """When no tracks have the connector ID, should return empty list."""
        tracks = [
            make_track(
                title="LastFM Only", connector_track_identifiers={"lastfm": "lf_1"}
            ),
            make_track(title="No IDs", connector_track_identifiers={}),
        ]

        items = create_connector_playlist_items_from_tracks(
            tracks=tracks,
            connector_name="spotify",
        )

        assert items == []

    def test_batch_passes_the_given_timestamp_to_every_item(self):
        tracks = [
            make_track(title="A", connector_track_identifiers={"spotify": "sp_a"}),
            make_track(title="B", connector_track_identifiers={"spotify": "sp_b"}),
        ]

        items = create_connector_playlist_items_from_tracks(
            tracks=tracks,
            connector_name="spotify",
            added_at=datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
        )

        assert [item.added_at for item in items] == [
            "2024-01-02T03:04:05+00:00",
            "2024-01-02T03:04:05+00:00",
        ]

    def test_batch_without_timestamp_stamps_every_item_with_one_now(
        self, ticking_clock: Mock
    ):
        """One clock read per batch, so items added together share a time."""
        tracks = [
            make_track(title="A", connector_track_identifiers={"spotify": "sp_a"}),
            make_track(title="B", connector_track_identifiers={"spotify": "sp_b"}),
            make_track(title="C", connector_track_identifiers={"spotify": "sp_c"}),
        ]

        items = create_connector_playlist_items_from_tracks(
            tracks=tracks,
            connector_name="spotify",
        )

        assert [item.added_at for item in items] == [
            "2025-06-15T12:00:00+00:00",
            "2025-06-15T12:00:00+00:00",
            "2025-06-15T12:00:00+00:00",
        ]
