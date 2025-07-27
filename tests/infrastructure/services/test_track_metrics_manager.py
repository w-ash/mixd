"""Tests for TrackMetricsManager infrastructure service.

Focused on the critical user flow: database-first metrics caching that prevents -inf values
when users sort tracks by Spotify popularity. Tests the regression fix from SCRATCHPAD.md.
"""

from unittest.mock import AsyncMock, Mock

import pytest

from src.domain.entities.track import Artist, Track, TrackList
from src.infrastructure.services.track_metrics_manager import TrackMetricsManager


class TestTrackMetricsManagerCriticalFlow:
    """Test the critical flow: database-first metrics retrieval prevents -inf values."""

    @pytest.fixture
    def mock_repos(self):
        """Create mock repositories."""
        track_repo = AsyncMock()
        connector_repo = AsyncMock()
        metrics_repo = AsyncMock()
        return track_repo, connector_repo, metrics_repo

    @pytest.fixture
    def mock_dependencies(self):
        """Create mock dependencies that TrackMetricsManager creates internally."""
        identity_resolver = AsyncMock()
        freshness_controller = AsyncMock()
        metadata_manager = AsyncMock()
        return identity_resolver, freshness_controller, metadata_manager

    @pytest.fixture
    def metrics_manager(self, mock_repos, mock_dependencies, monkeypatch):
        """Create TrackMetricsManager with mocked dependencies."""
        track_repo, connector_repo, metrics_repo = mock_repos
        identity_resolver, freshness_controller, metadata_manager = mock_dependencies
        
        manager = TrackMetricsManager(track_repo, connector_repo, metrics_repo)
        
        # Replace internal dependencies with mocks
        monkeypatch.setattr(manager, 'identity_resolver', identity_resolver)
        monkeypatch.setattr(manager, 'freshness_controller', freshness_controller)
        monkeypatch.setattr(manager, 'metadata_manager', metadata_manager)
        
        return manager

    @pytest.fixture
    def sample_tracks_with_ids(self):
        """Sample tracks with database IDs (realistic scenario)."""
        return TrackList(tracks=[
            Track(id=1, title="Popular Song", artists=[Artist(name="Artist 1")]),
            Track(id=2, title="Less Popular Song", artists=[Artist(name="Artist 2")]),
        ])

    @pytest.fixture
    def spotify_popularity_extractors(self):
        """Spotify popularity extractors (what users actually use)."""
        def extract_popularity(result):
            return result.service_data.get('popularity', 0)
        
        return {"spotify_popularity": extract_popularity}

    async def test_existing_metrics_prevent_api_calls_regression_prevention(
        self, metrics_manager, sample_tracks_with_ids, spotify_popularity_extractors, mock_repos, mock_dependencies
    ):
        """REGRESSION TEST: When tracks have existing metrics, no API calls are made.
        
        This test prevents the -inf bug described in SCRATCHPAD.md where the enricher
        was bypassing the database and always extracting from raw metadata.
        """
        # Arrange: Tracks have identity mappings
        identity_resolver, freshness_controller, metadata_manager = mock_dependencies
        _track_repo, _connector_repo, metrics_repo = mock_repos
        
        # Mock identity resolution (step 1)
        identity_mappings = {
            1: Mock(success=True, connector_id="spotify:123"),
            2: Mock(success=True, connector_id="spotify:456"),
        }
        identity_resolver.resolve_track_identities.return_value = identity_mappings
        
        # Mock existing metrics in database (step 2) - THE CRITICAL FIX
        existing_metrics = {
            "spotify_popularity": {
                1: 85,  # Real popularity value from database
                2: 67   # Real popularity value from database
            }
        }
        metrics_repo.get_track_metrics.return_value = existing_metrics["spotify_popularity"]
        
        # Mock freshness controller - no tracks are stale (step 4)
        freshness_controller.get_stale_tracks.return_value = []
        
        # Act: Enrich tracks
        _enriched_tracklist, metrics = await metrics_manager.enrich_tracks(
            sample_tracks_with_ids,
            "spotify",
            Mock(),
            spotify_popularity_extractors,
            max_age_hours=24.0
        )
        
        # Assert: Critical regression prevention
        assert "spotify_popularity" in metrics
        assert metrics["spotify_popularity"][1] == 85  # No -inf values!
        assert metrics["spotify_popularity"][2] == 67  # No -inf values!
        
        # Verify database was checked first
        metrics_repo.get_track_metrics.assert_called_once_with(
            track_ids=[1, 2],
            metric_type="spotify_popularity", 
            connector="spotify",
            max_age_hours=24.0
        )
        
        # Verify NO fresh metadata was fetched (the fix!)
        metadata_manager.fetch_fresh_metadata.assert_not_called()

    async def test_missing_metrics_trigger_fresh_fetch_only_for_missing(
        self, metrics_manager, sample_tracks_with_ids, spotify_popularity_extractors, mock_repos, mock_dependencies
    ):
        """Test that only tracks missing metrics trigger fresh API calls."""
        # Arrange
        identity_resolver, freshness_controller, metadata_manager = mock_dependencies
        _track_repo, _connector_repo, metrics_repo = mock_repos
        
        # Mock identity resolution
        identity_mappings = {
            1: Mock(success=True, connector_id="spotify:123"),
            2: Mock(success=True, connector_id="spotify:456"),
        }
        identity_resolver.resolve_track_identities.return_value = identity_mappings
        
        # Mock partial existing metrics - track 1 has metrics, track 2 doesn't
        existing_metrics = {"spotify_popularity": {1: 85}}  # Only track 1
        metrics_repo.get_track_metrics.return_value = existing_metrics["spotify_popularity"]
        
        # Mock freshness controller - track 2 needs refresh
        freshness_controller.get_stale_tracks.return_value = [2]
        
        # Mock fresh metadata fetch - only for track 2
        fresh_metadata = {2: {"popularity": 72}}
        metadata_manager.fetch_fresh_metadata.return_value = (fresh_metadata, [])
        
        # Act
        _enriched_tracklist, metrics = await metrics_manager.enrich_tracks(
            sample_tracks_with_ids,
            "spotify", 
            Mock(),
            spotify_popularity_extractors
        )
        
        # Assert: Combined existing + fresh metrics
        assert metrics["spotify_popularity"][1] == 85  # From database
        assert metrics["spotify_popularity"][2] == 72  # From fresh fetch
        
        # Verify fresh fetch was called only for track 2
        metadata_manager.fetch_fresh_metadata.assert_called_once()
        call_args = metadata_manager.fetch_fresh_metadata.call_args
        stale_track_ids = call_args[0][3]  # 4th positional argument
        assert stale_track_ids == [2]


class TestGetExistingMetrics:
    """Test the core _get_existing_metrics method that prevents the -inf regression."""

    @pytest.fixture
    def metrics_manager_simple(self):
        """Simple metrics manager for testing internal methods."""
        track_repo = AsyncMock()
        connector_repo = AsyncMock() 
        metrics_repo = AsyncMock()
        return TrackMetricsManager(track_repo, connector_repo, metrics_repo)

    async def test_get_existing_metrics_returns_database_values(self, metrics_manager_simple):
        """Test that _get_existing_metrics properly queries the database."""
        # Arrange
        track_ids = [1, 2, 3]
        requested_metrics = ["spotify_popularity", "spotify_energy"]
        
        # Mock database responses for each metric type
        popularity_values = {1: 85, 2: 67}
        energy_values = {1: 0.8, 3: 0.6}
        
        def mock_get_track_metrics(track_ids, metric_type, connector, max_age_hours):
            if metric_type == "spotify_popularity":
                return popularity_values
            elif metric_type == "spotify_energy":
                return energy_values
            return {}
        
        metrics_manager_simple.metrics_repo.get_track_metrics.side_effect = mock_get_track_metrics
        
        # Act
        result = await metrics_manager_simple._get_existing_metrics(
            track_ids, "spotify", requested_metrics, max_age_hours=24.0
        )
        
        # Assert
        assert result["spotify_popularity"] == popularity_values
        assert result["spotify_energy"] == energy_values
        
        # Verify repository was called correctly for each metric
        assert metrics_manager_simple.metrics_repo.get_track_metrics.call_count == 2

    async def test_get_existing_metrics_handles_empty_inputs(self, metrics_manager_simple):
        """Test graceful handling of edge cases."""
        # Test empty track IDs
        result = await metrics_manager_simple._get_existing_metrics([], "spotify", ["popularity"])
        assert result == {}
        
        # Test empty metrics list
        result = await metrics_manager_simple._get_existing_metrics([1, 2], "spotify", [])
        assert result == {}

    async def test_get_existing_metrics_handles_database_errors(self, metrics_manager_simple):
        """Test error handling when database queries fail."""
        # Arrange
        metrics_manager_simple.metrics_repo.get_track_metrics.side_effect = Exception("DB Error")
        
        # Act
        result = await metrics_manager_simple._get_existing_metrics(
            [1, 2], "spotify", ["spotify_popularity"]
        )
        
        # Assert: Should return empty dict, not crash
        assert result == {}


class TestMetricsPersistence:
    """Test metrics persistence to prevent future -inf values."""

    @pytest.fixture
    def metrics_manager_simple(self):
        """Simple metrics manager for testing persistence."""
        track_repo = AsyncMock()
        connector_repo = AsyncMock()
        metrics_repo = AsyncMock()
        return TrackMetricsManager(track_repo, connector_repo, metrics_repo)

    async def test_persist_metrics_saves_fresh_values(self, metrics_manager_simple):
        """Test that fresh metrics are properly saved to prevent future -inf values."""
        # Arrange
        fresh_metrics = {
            "spotify_popularity": {1: 85, 2: 67},
            "spotify_energy": {1: 0.8}
        }
        
        # Act
        await metrics_manager_simple._persist_metrics_to_database(fresh_metrics, "spotify")
        
        # Assert
        metrics_manager_simple.metrics_repo.save_track_metrics.assert_called_once()
        saved_batch = metrics_manager_simple.metrics_repo.save_track_metrics.call_args[0][0]
        
        # Verify the batch contains the expected metrics in correct format
        expected_batch = [
            (1, "spotify", "spotify_popularity", 85.0),
            (2, "spotify", "spotify_popularity", 67.0), 
            (1, "spotify", "spotify_energy", 0.8)
        ]
        assert len(saved_batch) == 3
        assert all(item in saved_batch for item in expected_batch)

    async def test_persist_metrics_handles_invalid_values(self, metrics_manager_simple):
        """Test that invalid metric values are filtered out."""
        # Arrange
        metrics_with_invalid = {
            "spotify_popularity": {
                1: 85,           # Valid
                2: "invalid",    # Invalid - string
                3: None          # Invalid - None
            }
        }
        
        # Act
        await metrics_manager_simple._persist_metrics_to_database(metrics_with_invalid, "spotify")
        
        # Assert: Only valid metrics are saved
        saved_batch = metrics_manager_simple.metrics_repo.save_track_metrics.call_args[0][0]
        assert len(saved_batch) == 1
        assert saved_batch[0] == (1, "spotify", "spotify_popularity", 85.0)