"""Tests for Spotify stale/dead track ID fallback resolution.

Diagnostic tests (real API) verify that dead IDs exist in old export data and
can be resolved via artist+title search. Unit tests (mocked) verify the
fallback logic in SpotifyInwardResolver.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.constants import MatchMethod
from src.infrastructure.connectors.spotify.client import SpotifyAPIClient
from src.infrastructure.connectors.spotify.inward_resolver import (
    FallbackHint,
    SpotifyInwardResolver,
)
from tests.fixtures import make_spotify_track, make_track


def _make_uow_mocks() -> tuple[MagicMock, AsyncMock, AsyncMock]:
    """Create UoW + repos. Returns (uow, track_repo, connector_repo)."""
    uow = MagicMock()
    track_repo = AsyncMock()
    connector_repo = AsyncMock()
    connector_repo.find_tracks_by_connectors.return_value = {}
    uow.get_track_repository.return_value = track_repo
    uow.get_connector_repository.return_value = connector_repo
    return uow, track_repo, connector_repo


class TestFallbackResolution:
    """Unit tests for dead-ID fallback via artist+title search (mocked)."""

    async def test_fallback_resolves_dead_id_via_search(self):
        """Dead IDs with hints should be resolved via search_track."""
        connector = AsyncMock()
        # API returns only id1, id2 is dead (not in response)
        connector.get_tracks_by_ids.return_value = {
            "id1": make_spotify_track("id1", "Song A"),
        }
        # Search returns a candidate for the dead id2
        search_result = make_spotify_track("new_id2", "Song B", "Artist B")
        connector.search_track.return_value = [search_result]

        resolver = SpotifyInwardResolver(spotify_connector=connector)
        uow, track_repo, connector_repo = _make_uow_mocks()

        saved_track_1 = make_track(id=1)
        saved_track_2 = make_track(id=2)
        track_repo.save_track.side_effect = [saved_track_1, saved_track_2]
        connector_repo.map_track_to_connector.return_value = saved_track_1

        hints = {
            "id2": FallbackHint(artist_name="Artist B", track_name="Song B"),
        }

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id1", "id2"], uow, fallback_hints=hints
        )

        assert "id1" in result
        assert "id2" in result
        assert result["id2"] == saved_track_2
        connector.search_track.assert_called_once_with("Artist B", "Song B")

    async def test_fallback_creates_secondary_mapping_for_dead_id(self):
        """Fallback should create both primary (new ID) and secondary (dead ID) mappings."""
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {}  # All IDs dead
        search_result = make_spotify_track("new_id", "Song", "Artist")
        connector.search_track.return_value = [search_result]

        resolver = SpotifyInwardResolver(spotify_connector=connector)
        uow, track_repo, connector_repo = _make_uow_mocks()

        saved_track = make_track(id=1)
        track_repo.save_track.return_value = saved_track
        connector_repo.map_track_to_connector.return_value = saved_track

        hints = {"dead_id": FallbackHint(artist_name="Artist", track_name="Song")}

        await resolver.resolve_to_canonical_tracks(
            ["dead_id"], uow, fallback_hints=hints
        )

        # Should have 2 map_track_to_connector calls: primary (new_id) + secondary (dead_id)
        map_calls = connector_repo.map_track_to_connector.call_args_list
        assert len(map_calls) == 2

        # Primary mapping for new Spotify ID
        primary_call = map_calls[0]
        assert primary_call.args[:4] == (
            saved_track,
            "spotify",
            "new_id",
            MatchMethod.SEARCH_FALLBACK,
        )
        assert primary_call.kwargs["auto_set_primary"] is True
        # Confidence is now domain-calculated, not a hardcoded constant
        assert isinstance(primary_call.kwargs["confidence"], int)
        assert primary_call.kwargs["confidence"] > 0

        # Secondary mapping for dead ID
        secondary_call = map_calls[1]
        assert secondary_call.args[:4] == (
            saved_track,
            "spotify",
            "dead_id",
            "search_fallback_stale_id",
        )
        assert secondary_call.kwargs["auto_set_primary"] is False
        assert secondary_call.kwargs["confidence"] == primary_call.kwargs["confidence"]

    async def test_fallback_skipped_when_no_hints(self):
        """Dead IDs without hints should remain as failures."""
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {}  # All dead

        resolver = SpotifyInwardResolver(spotify_connector=connector)
        uow, _, connector_repo = _make_uow_mocks()

        # No fallback hints provided
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["dead_id"], uow, fallback_hints=None
        )

        assert "dead_id" not in result
        assert metrics.failed == 1
        connector.search_track.assert_not_called()

    async def test_fallback_rejects_low_similarity_match(self):
        """Search results with low title similarity should be rejected."""
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {}
        # Search returns a completely different track
        connector.search_track.return_value = [
            make_spotify_track("other_id", "Completely Different Song", "Other Artist")
        ]

        resolver = SpotifyInwardResolver(spotify_connector=connector)
        uow, _, _ = _make_uow_mocks()

        hints = {
            "dead_id": FallbackHint(
                artist_name="Original Artist", track_name="Original Song"
            )
        }

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["dead_id"], uow, fallback_hints=hints
        )

        assert "dead_id" not in result
        assert metrics.failed == 1

    async def test_fallback_search_failure_graceful(self):
        """Search errors should be caught gracefully, not crash the batch."""
        connector = AsyncMock()
        # id1 resolves via API normally
        connector.get_tracks_by_ids.return_value = {
            "id1": make_spotify_track("id1", "Song A"),
        }
        # Search for id2 raises an exception
        connector.search_track.side_effect = RuntimeError("API error")

        resolver = SpotifyInwardResolver(spotify_connector=connector)
        uow, track_repo, connector_repo = _make_uow_mocks()

        saved_track = make_track(id=1)
        track_repo.save_track.return_value = saved_track
        connector_repo.map_track_to_connector.return_value = saved_track

        hints = {"id2": FallbackHint(artist_name="Artist B", track_name="Song B")}

        # Should not raise — id1 is still resolved, id2 fails gracefully
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id1", "id2"], uow, fallback_hints=hints
        )

        assert "id1" in result
        assert "id2" not in result

    async def test_fallback_uses_isrc_dedup(self):
        """Search results whose ISRC already exists should upsert, not duplicate."""
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {}
        connector.search_track.return_value = [
            make_spotify_track("new_id", "Song", "Artist", isrc="USRC12345678")
        ]

        resolver = SpotifyInwardResolver(spotify_connector=connector)
        uow, track_repo, connector_repo = _make_uow_mocks()

        # save_track returns the EXISTING track (ISRC upsert)
        existing_track = make_track(id=42, isrc="USRC12345678")
        track_repo.save_track.return_value = existing_track
        connector_repo.map_track_to_connector.return_value = existing_track

        hints = {"dead_id": FallbackHint(artist_name="Artist", track_name="Song")}

        result, _ = await resolver.resolve_to_canonical_tracks(
            ["dead_id"], uow, fallback_hints=hints
        )

        assert "dead_id" in result
        assert result["dead_id"].id == 42  # Existing track, not a new one
        track_repo.save_track.assert_called_once()  # Upsert, not create + duplicate

    async def test_fallback_resolved_ids_exposed(self):
        """fallback_resolved_ids should contain only IDs resolved via search."""
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {
            "id1": make_spotify_track("id1"),
        }
        connector.search_track.return_value = [make_spotify_track("new_id2", "Song B")]

        resolver = SpotifyInwardResolver(spotify_connector=connector)
        uow, track_repo, connector_repo = _make_uow_mocks()

        track_repo.save_track.side_effect = [make_track(1), make_track(2)]
        connector_repo.map_track_to_connector.return_value = make_track(1)

        hints = {"id2": FallbackHint(artist_name="Artist", track_name="Song B")}

        await resolver.resolve_to_canonical_tracks(
            ["id1", "id2"], uow, fallback_hints=hints
        )

        assert resolver.fallback_resolved_ids == {"id2"}

    async def test_fallback_empty_search_results_not_resolved(self):
        """Empty search results should leave the ID unresolved."""
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {}
        connector.search_track.return_value = []

        resolver = SpotifyInwardResolver(spotify_connector=connector)
        uow, _, _ = _make_uow_mocks()

        hints = {"dead_id": FallbackHint(artist_name="Artist", track_name="Song")}

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["dead_id"], uow, fallback_hints=hints
        )

        assert "dead_id" not in result
        assert metrics.failed == 1


@pytest.mark.diagnostic
class TestStaleIdDiscovery:
    """Diagnostic tests using real Spotify API. Run with: pytest -m diagnostic"""

    @pytest.fixture
    def export_file(self) -> Path | None:
        path = Path("data/imports/Streaming_History_Audio_2011-2014_0.json")
        if not path.exists():
            pytest.skip("Export file not available")
        return path

    async def test_oldest_export_has_dead_ids(self, export_file):
        """Parse old export, sample 20 IDs, verify at least 1 is dead."""
        import random

        from src.infrastructure.connectors.spotify.personal_data import (
            parse_spotify_personal_data,
        )

        records = parse_spotify_personal_data(export_file)

        # Extract unique track IDs
        id_map: dict[str, tuple[str, str]] = {}
        for r in records:
            parts = r.track_uri.split(":")
            if len(parts) == 3:
                id_map[parts[2]] = (r.artist_name, r.track_name)

        sample_ids = random.sample(list(id_map.keys()), min(20, len(id_map)))

        client = SpotifyAPIClient()
        dead_count = 0
        try:
            for track_id in sample_ids:
                result = await client.get_track(track_id)
                if not result:
                    dead_count += 1
        finally:
            await client.aclose()

        # At least 1 dead ID expected in old export data
        assert dead_count >= 1, (
            f"Expected dead IDs in old export, but all {len(sample_ids)} were alive"
        )

    async def test_dead_id_resolvable_by_search(self, export_file):
        """For dead IDs, verify artist+title search returns results with ISRCs."""
        import random

        from src.infrastructure.connectors.spotify.personal_data import (
            parse_spotify_personal_data,
        )

        records = parse_spotify_personal_data(export_file)

        id_map: dict[str, tuple[str, str]] = {}
        for r in records:
            parts = r.track_uri.split(":")
            if len(parts) == 3:
                id_map[parts[2]] = (r.artist_name, r.track_name)

        sample_ids = random.sample(list(id_map.keys()), min(30, len(id_map)))

        client = SpotifyAPIClient()
        dead_ids: list[str] = []
        try:
            for track_id in sample_ids:
                result = await client.get_track(track_id)
                if not result:
                    dead_ids.append(track_id)

            if not dead_ids:
                pytest.skip(
                    "No dead IDs found in sample — increase sample or try older export"
                )

            # For each dead ID, try search
            resolved = 0
            for dead_id in dead_ids[:5]:  # Limit to 5 to avoid rate limits
                artist, title = id_map[dead_id]
                candidates = await client.search_track(artist, title)
                if candidates:
                    resolved += 1
                    # Verify top result has ISRC
                    top = candidates[0]
                    assert top.external_ids.isrc, (
                        f"Search result for {artist} - {title} has no ISRC"
                    )

            assert resolved > 0, "No dead IDs could be resolved via search"
        finally:
            await client.aclose()
