"""Tests for Spotify track relinking propagation in conversions.

Verifies that linked_from.id is preserved in raw_metadata when Spotify
relinks a track to a different market-appropriate ID.
"""

from src.infrastructure.connectors.spotify.conversions import (
    convert_spotify_track_to_connector,
)


def _base_spotify_track(
    track_id: str = "new_id_B",
    linked_from: dict[str, str] | None = None,
) -> dict:
    """Build minimal Spotify track dict for conversion testing."""
    data: dict = {
        "id": track_id,
        "name": "Test Song",
        "artists": [{"name": "Test Artist"}],
        "album": {"name": "Test Album", "id": "album1"},
        "duration_ms": 200_000,
        "popularity": 50,
        "explicit": False,
        "external_ids": {"isrc": "USRC12345678"},
    }
    if linked_from is not None:
        data["linked_from"] = linked_from
    return data


class TestConvertSpotifyTrackRelinking:
    """Tests for linked_from propagation in convert_spotify_track_to_connector."""

    def test_convert_without_linked_from(self):
        """No linked_from_id key should exist in raw_metadata for normal tracks."""
        track_data = _base_spotify_track()
        result = convert_spotify_track_to_connector(track_data)

        assert "linked_from_id" not in result.raw_metadata

    def test_convert_with_linked_from_stores_id(self):
        """linked_from.id should be stored in raw_metadata as linked_from_id."""
        track_data = _base_spotify_track(
            track_id="new_id_B",
            linked_from={"id": "original_id_A"},
        )
        result = convert_spotify_track_to_connector(track_data)

        assert result.raw_metadata["linked_from_id"] == "original_id_A"

    def test_connector_track_identifier_is_response_id(self):
        """Even with linked_from, identifier must use the response track.id (market-appropriate)."""
        track_data = _base_spotify_track(
            track_id="new_id_B",
            linked_from={"id": "original_id_A"},
        )
        result = convert_spotify_track_to_connector(track_data)

        assert result.connector_track_identifier == "new_id_B"

    def test_linked_from_does_not_overwrite_existing_metadata(self):
        """linked_from_id should coexist with other raw_metadata fields."""
        track_data = _base_spotify_track(
            track_id="new_id_B",
            linked_from={"id": "original_id_A"},
        )
        result = convert_spotify_track_to_connector(track_data)

        # Standard metadata fields should still be present
        assert "popularity" in result.raw_metadata
        assert "explicit" in result.raw_metadata
        assert result.raw_metadata["linked_from_id"] == "original_id_A"
