"""Tests for Spotify stale/dead track ID discovery (diagnostic, real API).

These tests use the real Spotify API to verify that dead track IDs exist in
old export data and can be resolved via artist+title search. Run with:
    pytest -m diagnostic
"""

from pathlib import Path

import pytest

from src.infrastructure.connectors.spotify.client import SpotifyAPIClient


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
