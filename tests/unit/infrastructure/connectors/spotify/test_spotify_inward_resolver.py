"""Tests for SpotifyInwardResolver.

Validates that the Spotify-specific inward resolver correctly batches API calls,
handles relinking, and delegates bulk lookup to the base class.
"""

from unittest.mock import AsyncMock, MagicMock

from src.infrastructure.connectors.spotify.inward_resolver import (
    SpotifyInwardResolver,
)
from tests.fixtures import make_track


def _make_spotify_data(
    spotify_id: str,
    name: str = "Test",
    linked_from: dict | None = None,
) -> dict:
    """Create minimal Spotify API track data."""
    data: dict = {
        "id": spotify_id,
        "name": name,
        "artists": [{"name": "Artist"}],
        "album": {"name": "Album"},
        "duration_ms": 240000,
    }
    if linked_from:
        data["linked_from"] = linked_from
    return data


class TestBatchFetch:
    """SpotifyInwardResolver should batch its API call."""

    async def test_calls_get_tracks_by_ids_once(self):
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {
            "id1": _make_spotify_data("id1", "Song A"),
            "id2": _make_spotify_data("id2", "Song B"),
        }

        resolver = SpotifyInwardResolver(spotify_connector=connector)

        uow = MagicMock()
        track_repo = AsyncMock()
        track_repo.save_track.side_effect = [make_track(1), make_track(2)]
        uow.get_track_repository.return_value = track_repo

        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        connector_repo.map_track_to_connector.return_value = make_track(1)
        uow.get_connector_repository.return_value = connector_repo

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id1", "id2"], uow
        )

        connector.get_tracks_by_ids.assert_called_once()
        assert len(result) == 2
        assert metrics.created == 2

    async def test_existing_tracks_not_refetched(self):
        """Tracks with existing connector mappings should not trigger API calls."""
        connector = AsyncMock()
        existing = make_track(42)

        resolver = SpotifyInwardResolver(spotify_connector=connector)

        uow = MagicMock()
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {
            ("spotify", "id1"): existing,
        }
        uow.get_connector_repository.return_value = connector_repo

        result, metrics = await resolver.resolve_to_canonical_tracks(["id1"], uow)

        connector.get_tracks_by_ids.assert_not_called()
        assert result["id1"] == existing
        assert metrics.existing == 1
        assert metrics.created == 0


class TestRelinking:
    """Spotify relinking should create both primary and secondary mappings."""

    async def test_relinking_creates_dual_mappings(self):
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {
            "old_id_12345678901234": _make_spotify_data(
                "new_id_12345678901234",
                linked_from={"id": "old_id_12345678901234"},
            ),
        }

        saved_track = make_track(55)
        resolver = SpotifyInwardResolver(spotify_connector=connector)

        uow = MagicMock()
        track_repo = AsyncMock()
        track_repo.save_track.return_value = saved_track
        uow.get_track_repository.return_value = track_repo

        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        connector_repo.map_track_to_connector.return_value = saved_track
        uow.get_connector_repository.return_value = connector_repo

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["old_id_12345678901234"], uow
        )

        # Two map_track_to_connector calls: primary (new) + secondary (old)
        map_calls = connector_repo.map_track_to_connector.call_args_list
        assert len(map_calls) == 2

        # Primary mapping uses the API response ID
        assert map_calls[0].args[2] == "new_id_12345678901234"
        assert map_calls[0].kwargs["auto_set_primary"] is True

        # Secondary mapping uses the linked_from ID
        assert map_calls[1].args[2] == "old_id_12345678901234"
        assert map_calls[1].kwargs["auto_set_primary"] is False


class TestMissingMetadata:
    """IDs not returned by Spotify API should be reported as failed."""

    async def test_missing_api_response_counted_as_failed(self):
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {
            "id1": _make_spotify_data("id1"),
            # id2 intentionally missing from API response
        }

        resolver = SpotifyInwardResolver(spotify_connector=connector)

        uow = MagicMock()
        track_repo = AsyncMock()
        track_repo.save_track.return_value = make_track(1)
        uow.get_track_repository.return_value = track_repo

        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        connector_repo.map_track_to_connector.return_value = make_track(1)
        uow.get_connector_repository.return_value = connector_repo

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id1", "id2"], uow
        )

        assert "id1" in result
        assert "id2" not in result
        assert metrics.created == 1
        assert metrics.failed == 1


class TestNormalization:
    """Spotify IDs are pass-through (already stable)."""

    async def test_spotify_id_passthrough(self):
        connector = AsyncMock()
        resolver = SpotifyInwardResolver(spotify_connector=connector)

        # Spotify IDs should be passed as-is
        assert (
            resolver._normalize_id("4iV5W9uYEdYUVa79Axb7Rh") == "4iV5W9uYEdYUVa79Axb7Rh"
        )
