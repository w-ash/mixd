"""Tests for Spotify stale/dead track ID discovery (diagnostic, real API).

These tests use the real Spotify API to verify that dead track IDs exist in
old export data and can be resolved via artist+title search.

Diagnostic only — opt in by setting ``MIXD_DIAGNOSTIC_RUN=1`` and running
against a developer environment with a stored Spotify OAuth token plus a
local Spotify Wrapped export file. Without the opt-in, these tests skip:
under the pytest harness, the ``_init_test_schema`` fixture redirects
``DATABASE_URL`` to a fresh testcontainers Postgres with no token, so the
Spotify client would fall back to launching the interactive browser OAuth
flow — never an acceptable side effect of an automated test run.
"""

from collections.abc import Sequence
import os
from pathlib import Path
import random

import pytest

from src.infrastructure.connectors.spotify.client import (
    SpotifyAPIClient,
    SpotifyTracksFetch,
    field_filtered_search_query,
)


def _sample_export_ids(
    export_file: Path, sample_size: int
) -> tuple[list[str], dict[str, tuple[str, str]]]:
    """Sample unique track ids from an export, with their (artist, title)."""
    from src.infrastructure.connectors.spotify.personal_data import (
        parse_spotify_personal_data,
    )

    id_map: dict[str, tuple[str, str]] = {}
    for r in parse_spotify_personal_data(export_file):
        parts = r.track_uri.split(":")
        if len(parts) == 3:
            id_map[parts[2]] = (r.artist_name, r.track_name)

    sample_ids = random.sample(list(id_map.keys()), min(sample_size, len(id_map)))
    return sample_ids, id_map


def _dead_ids(requested: Sequence[str], fetch: SpotifyTracksFetch) -> list[str]:
    """Ids Spotify answered about with null — absent from tracks AND unanswered.

    An unanswered id is a request that never landed, so counting it as dead
    would manufacture deaths out of one 5xx.
    """
    return [
        track_id
        for track_id in requested
        if track_id not in fetch.tracks and track_id not in fetch.unanswered
    ]


@pytest.mark.diagnostic
class TestStaleIdDiscovery:
    """Diagnostic tests using real Spotify API. Run by hand against a real env."""

    @pytest.fixture
    def export_file(self) -> Path | None:
        if os.environ.get("MIXD_DIAGNOSTIC_RUN") != "1":
            pytest.skip(
                "Diagnostic requires MIXD_DIAGNOSTIC_RUN=1 plus a real "
                "DATABASE_URL with a stored Spotify token"
            )
        path = Path("data/imports/Streaming_History_Audio_2011-2014_0.json")
        if not path.exists():
            pytest.skip("Export file not available")
        return path

    async def test_oldest_export_has_dead_ids(self, export_file):
        """Parse old export, ask about 20 IDs in one batch, expect ≥1 dead."""
        sample_ids, _ = _sample_export_ids(export_file, 20)

        client = SpotifyAPIClient()
        try:
            fetch = await client.get_tracks_batched(sample_ids)
        finally:
            await client.aclose()

        if fetch.unanswered:
            pytest.skip(
                f"{len(fetch.unanswered)}/{len(sample_ids)} IDs went unanswered — "
                "the API did not answer, so nothing can be concluded; re-run"
            )

        assert _dead_ids(sample_ids, fetch), (
            f"Expected dead IDs in old export, but all {len(sample_ids)} were alive"
        )

    async def test_dead_id_resolvable_by_search(self, export_file):
        """For dead IDs, verify artist+title search returns results with ISRCs."""
        sample_ids, id_map = _sample_export_ids(export_file, 30)

        client = SpotifyAPIClient()
        try:
            fetch = await client.get_tracks_batched(sample_ids)
            dead_ids = _dead_ids(sample_ids, fetch)

            if not dead_ids:
                pytest.skip(
                    "No dead IDs found in sample — increase sample or try older export"
                )

            # For each dead ID, try search
            resolved = 0
            for dead_id in dead_ids[:5]:  # Limit to 5 to avoid rate limits
                artist, title = id_map[dead_id]
                candidates = await client.search_track(
                    field_filtered_search_query(artist, title)
                )
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
