"""Integration tests for the complete track metrics workflow.

Tests the end-to-end user flow: loading tracks from playlists, enriching with
Spotify popularity metrics, and ensuring no -inf values appear when sorting.
This validates the database-first caching fix from SCRATCHPAD.md.

Uses real business logic with automatic database rollback for safety.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.domain.entities.track import Artist, Track, TrackList
from src.infrastructure.persistence.repositories.factories import (
    get_connector_repository,
    get_metrics_repository,
    get_track_repository,
)
from src.infrastructure.services.track_metrics_manager import TrackMetricsManager


@pytest.mark.asyncio
class TestTrackMetricsWorkflowIntegration:
    """Integration tests for the complete track metrics workflow with real business logic."""

    async def _setup_connector_mappings(self, connector_repo, saved_tracks):
        """Helper to set up connector mappings for tracks to avoid identity resolution."""
        for i, saved_track in enumerate(saved_tracks):
            await connector_repo.map_track_to_connector(
                track=saved_track,
                connector="spotify",
                connector_id=f"spotify:track:test{i + 1}",
                match_method="exact",
                confidence=1.0,
                metadata={},
            )

    async def test_spotify_popularity_workflow_prevents_inf_values_regression(
        self, db_session
    ):
        """Integration test: Tracks with existing metrics should never show -inf values.

        This test simulates the exact user workflow from test_spotify_popularity_sort.json:
        1. Users load tracks from playlists (tracks in database)
        2. Previous workflow run stored metrics in track_metrics table
        3. Users run workflow again expecting to use cached metrics
        4. System should use database-first caching (THE CRITICAL FIX)
        5. No external API calls needed, no -inf values

        Tests REAL business logic with automatic database rollback for safety.
        """
        # Setup: Create real repositories with safe rollback session
        track_repo = get_track_repository(db_session)
        connector_repo = get_connector_repository(db_session)
        metrics_repo = get_metrics_repository(db_session)

        # Step 1: Create tracks in database (simulating playlist import)
        test_tracks = [
            Track(
                id=None,  # Will be assigned by database
                title="High Popularity Song",
                artists=[Artist(name="Popular Artist")],
                connector_track_ids={"spotify": "spotify:track:popular123"},
            ),
            Track(
                id=None,
                title="Medium Popularity Song",
                artists=[Artist(name="Medium Artist")],
                connector_track_ids={"spotify": "spotify:track:medium456"},
            ),
        ]

        # Save tracks to database (will be rolled back automatically)
        saved_tracks = []
        for track in test_tracks:
            saved_track = await track_repo.save_track(track)
            saved_tracks.append(saved_track)
        track_ids = [t.id for t in saved_tracks]

        # Step 2a: Pre-populate connector mappings (simulating previous identity resolution)
        # This prevents the system from trying to resolve identities via external API
        await self._setup_connector_mappings(connector_repo, saved_tracks)

        # Step 2b: Pre-populate metrics table with known good values
        # (simulating previous successful workflow run)
        metrics_batch = [
            (track_ids[0], "spotify", "spotify_popularity", 85.0),
            (track_ids[1], "spotify", "spotify_popularity", 67.0),
        ]
        await metrics_repo.save_track_metrics(metrics_batch)

        # Step 3: Create real TrackMetricsManager with real business logic
        metrics_manager = TrackMetricsManager(track_repo, connector_repo, metrics_repo)

        # Step 4: Mock ONLY external API boundary (not business logic)
        mock_connector = Mock()
        mock_connector.batch_get_track_info = AsyncMock(return_value={})

        # Step 5: Execute real enrichment workflow
        tracklist = TrackList(tracks=saved_tracks)

        def extract_popularity(result):
            return result.service_data.get("popularity", 0)

        extractors = {"spotify_popularity": extract_popularity}

        enriched_tracklist, metrics = await metrics_manager.enrich_tracks(
            tracklist, "spotify", mock_connector, extractors, max_age_hours=24.0
        )

        # Step 6: Verify critical regression prevention - NO -inf VALUES!
        assert "spotify_popularity" in metrics, (
            "Spotify popularity metrics should be present"
        )
        popularity_values = metrics["spotify_popularity"]

        # Verify all popularity values are real numbers from database, not -inf
        for track_id, popularity in popularity_values.items():
            assert popularity > 0, (
                f"Track {track_id} has invalid popularity: {popularity}"
            )
            assert popularity != float("-inf"), (
                f"Track {track_id} has -inf popularity (REGRESSION)!"
            )
            assert isinstance(popularity, (int, float)), (
                f"Track {track_id} popularity not numeric: {type(popularity)}"
            )

        # Verify specific expected values retrieved from database
        assert popularity_values[track_ids[0]] == 85, (
            "Track 1 should have database value 85"
        )
        assert popularity_values[track_ids[1]] == 67, (
            "Track 2 should have database value 67"
        )

        # Step 7: Verify database-first caching worked (THE CRITICAL FIX)
        mock_connector.batch_get_track_info.assert_not_called()

        # Step 8: Verify enriched tracklist has metrics attached correctly
        tracklist_metrics = enriched_tracklist.metadata.get("metrics", {})
        assert "spotify_popularity" in tracklist_metrics
        assert tracklist_metrics["spotify_popularity"] == popularity_values

        # No cleanup needed - automatic rollback handles everything!

    async def test_metrics_enrichment_workflow_robustness(self, db_session):
        """Integration test: Metrics enrichment handles various scenarios robustly.

        This test verifies that the enrichment workflow:
        1. Completes without exceptions
        2. Returns valid, non-negative values
        3. Never returns -inf values (key regression prevention)
        """
        # Setup: Create real repositories
        track_repo = get_track_repository(db_session)
        connector_repo = get_connector_repository(db_session)
        metrics_repo = get_metrics_repository(db_session)

        # Create test track with unique identifier
        import uuid

        unique_id = str(uuid.uuid4())[:8]
        connector_id = f"spotify:track:robust{unique_id}"
        
        # Create track with connector ID already set
        test_track = Track(
            id=None,
            title=f"Robustness Test Track {unique_id}",
            artists=[Artist(name=f"Robustness Test Artist {unique_id}")],
            connector_track_ids={"spotify": connector_id},
        )

        saved_track = await track_repo.save_track(test_track)
        track_id = saved_track.id

        # Set up connector mapping with the same ID as the track
        await connector_repo.map_track_to_connector(
            track=saved_track,
            connector="spotify",
            connector_id=connector_id,
            match_method="exact", 
            confidence=1.0,
            metadata={"popularity": 75},  # Add some metadata that can be used
        )

        # Create real TrackMetricsManager
        metrics_manager = TrackMetricsManager(track_repo, connector_repo, metrics_repo)

        # Mock external API with reasonable data - use AsyncMock for proper async compatibility
        mock_connector = AsyncMock()
        mock_connector.batch_get_track_info.return_value = {track_id: {"popularity": 75}}
        mock_connector.connector_name = "spotify"
        
        # Mock search methods to return proper track data that matches our test track
        mock_track_data = {
            "id": connector_id,
            "name": f"Robustness Test Track {unique_id}",
            "artists": [{"name": f"Robustness Test Artist {unique_id}"}],
            "popularity": 75,
            "album": {"name": "Test Album"},
        }
        
        mock_connector.search_tracks = AsyncMock(return_value=[mock_track_data])
        mock_connector.batch_search_tracks = AsyncMock(return_value={track_id: [mock_track_data]})

        # Execute enrichment workflow
        tracklist = TrackList(tracks=[saved_track])

        def extract_popularity(result):
            return result.service_data.get("popularity", 0)

        extractors = {"spotify_popularity": extract_popularity}

        # Mock the entire track identity service workflow to bypass resolution issues
        with patch("src.infrastructure.services.track_metrics_manager.MatchAndIdentifyTracksUseCase") as mock_use_case_class:
            mock_identity_result = Mock()
            mock_identity_result.identity_mappings = {track_id: connector_id}
            mock_identity_result.result_status = "success"
            
            mock_use_case_instance = AsyncMock()
            mock_use_case_instance.execute.return_value = mock_identity_result
            mock_use_case_class.return_value = mock_use_case_instance
            
            # This should complete without exceptions
            enriched_tracklist, metrics = await metrics_manager.enrich_tracks(
                tracklist, "spotify", mock_connector, extractors
            )

        # Verify successful completion with valid data
        assert "spotify_popularity" in metrics, "Should have spotify_popularity metrics"
        assert track_id in metrics["spotify_popularity"], (
            "Should have metrics for the test track"
        )

        # Verify no invalid values (the key regression prevention)
        popularity_value = metrics["spotify_popularity"][track_id]
        assert isinstance(popularity_value, (int, float)), (
            "Popularity should be numeric"
        )
        assert popularity_value != float("-inf"), (
            "Should never return -inf values (CRITICAL)"
        )
        assert popularity_value >= 0, "Popularity should be non-negative"
        assert popularity_value <= 100, "Spotify popularity should be 0-100 range"

        # Verify enriched tracklist structure
        assert enriched_tracklist is not None, "Should return enriched tracklist"
        assert len(enriched_tracklist.tracks) == 1, "Should have one track"
        assert "metrics" in enriched_tracklist.metadata, "Should have metrics metadata"
