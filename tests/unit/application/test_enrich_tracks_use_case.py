"""Tests for EnrichTracksUseCase application layer.

This test suite validates the core enrichment orchestration logic following
Clean Architecture principles with proper mocking at architectural boundaries.
Tests use UnitOfWork pattern for proper Clean Architecture compliance.
"""

from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.application.use_cases.enrich_tracks import (
    EnrichmentConfig,
    EnrichTracksCommand,
    EnrichTracksResult,
    EnrichTracksUseCase,
)
from src.domain.entities.track import Artist, Track, TrackList


class TestEnrichTracksUseCase:
    """Test suite for EnrichTracksUseCase."""

    @pytest.fixture
    def mock_plays_repo(self):
        """Mock plays repository."""
        mock = AsyncMock()
        mock.get_play_aggregations.return_value = {
            "total_plays": {1: 42, 2: 15},
            "last_played_dates": {1: "2023-01-15", 2: "2023-01-10"},
        }
        return mock

    @pytest.fixture
    def mock_uow(self, mock_plays_repo):
        """Mock UnitOfWork with required services."""
        mock = Mock()
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=None)
        mock.get_plays_repository.return_value = mock_plays_repo
        return mock

    @pytest.fixture
    def use_case(self):
        """Create EnrichTracksUseCase instance (no constructor dependencies)."""
        return EnrichTracksUseCase()

    @pytest.fixture
    def sample_tracklist(self):
        """Create sample tracklist for testing."""
        tracks = [
            Track(id=1, title="Test Song 1", artists=[Artist(name="Artist 1")]),
            Track(id=2, title="Test Song 2", artists=[Artist(name="Artist 2")]),
        ]
        return TrackList(tracks=tracks)

    @pytest.fixture
    def external_metadata_config(self):
        """Create external metadata enrichment config."""
        return EnrichmentConfig(
            enrichment_type="external_metadata",
            connector="spotify",
            connector_instance=Mock(),
            track_metric_names=["spotify_popularity"],
        )

    @pytest.fixture
    def play_history_config(self):
        """Create play history enrichment config."""
        return EnrichmentConfig(
            enrichment_type="play_history",
            metrics=["total_plays", "last_played_dates"],
            period_days=30,
        )

    @patch("src.application.use_cases.enrich_tracks.MetricsApplicationService")
    async def test_external_metadata_enrichment_success(
        self,
        mock_metrics_service_class,
        use_case,
        sample_tracklist,
        external_metadata_config,
        mock_uow,
    ):
        """Test successful external metadata enrichment."""
        # Arrange
        expected_metrics = {"spotify_popularity": {1: 85, 2: 92}}
        expected_fresh_ids = {"spotify_popularity": {1, 2}}

        # Mock the MetricsApplicationService instance
        mock_metrics_service = AsyncMock()
        mock_metrics_service.get_external_track_metrics.return_value = (
            expected_metrics,
            expected_fresh_ids,
        )
        mock_metrics_service_class.return_value = mock_metrics_service

        command = EnrichTracksCommand(
            tracklist=sample_tracklist, enrichment_config=external_metadata_config
        )

        # Act
        result = await use_case.execute(command, mock_uow)

        # Assert
        assert isinstance(result, EnrichTracksResult)
        assert result.metrics_added == expected_metrics
        assert result.track_count == 2
        assert result.enriched_count == 2  # Total values across all metrics
        assert len(result.errors) == 0

        # Verify MetricsApplicationService was called correctly
        mock_metrics_service.get_external_track_metrics.assert_called_once_with(
            track_ids=[1, 2],  # Sample tracklist has tracks with IDs 1 and 2
            connector="spotify",
            metric_names=["spotify_popularity"],  # From track_metric_names
            uow=mock_uow,
            connector_instance=external_metadata_config.connector_instance,
        )

    async def test_play_history_enrichment_success(
        self, use_case, sample_tracklist, play_history_config, mock_uow, mock_plays_repo
    ):
        """Test successful play history enrichment."""
        # Arrange
        command = EnrichTracksCommand(
            tracklist=sample_tracklist, enrichment_config=play_history_config
        )

        # Act
        result = await use_case.execute(command, mock_uow)

        # Assert
        assert isinstance(result, EnrichTracksResult)
        assert result.track_count == 2
        assert result.enriched_count == 4  # 2 metrics * 2 tracks (from mock fixture)
        assert len(result.errors) == 0

        # Verify play repository was called correctly
        mock_plays_repo.get_play_aggregations.assert_called_once_with(
            track_ids=[1, 2],
            metrics=["total_plays", "last_played_dates"],
            period_start=None,
            period_end=None,
        )

    async def test_play_history_with_period_calculation(self, sample_tracklist):
        """Test play history enrichment with period boundaries."""
        # Create custom mock for this test
        mock_play_repo = AsyncMock()
        mock_play_repo.get_play_aggregations.return_value = {
            "period_plays": {1: 3, 2: 5}
        }

        mock_uow = Mock()
        mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
        mock_uow.__aexit__ = AsyncMock(return_value=None)
        mock_uow.get_plays_repository.return_value = mock_play_repo

        use_case = EnrichTracksUseCase()

        # Arrange
        config = EnrichmentConfig(
            enrichment_type="play_history", metrics=["period_plays"], period_days=7
        )
        command = EnrichTracksCommand(
            tracklist=sample_tracklist, enrichment_config=config
        )

        # Act
        await use_case.execute(command, mock_uow)

        # Assert - verify period_start and period_end were calculated
        mock_play_repo.get_play_aggregations.assert_called_once()
        _call_args, call_kwargs = mock_play_repo.get_play_aggregations.call_args

        assert call_kwargs["track_ids"] == [1, 2]
        assert call_kwargs["metrics"] == ["period_plays"]

        # Check that period boundaries were calculated (should be datetime objects)
        period_start = call_kwargs["period_start"]
        period_end = call_kwargs["period_end"]
        assert period_start is not None
        assert period_end is not None

        assert isinstance(period_start, datetime)
        assert isinstance(period_end, datetime)

        # Check that the period is approximately 7 days
        period_duration = period_end - period_start
        assert abs(period_duration.days - 7) <= 1  # Allow for small timing differences

    async def test_empty_tracklist_handling(
        self, use_case, external_metadata_config, mock_uow
    ):
        """Test handling of empty tracklist."""
        # Arrange
        empty_tracklist = TrackList(tracks=[])
        command = EnrichTracksCommand(
            tracklist=empty_tracklist, enrichment_config=external_metadata_config
        )

        # Act
        result = await use_case.execute(command, mock_uow)

        # Assert
        assert result.enriched_tracklist == empty_tracklist
        assert result.metrics_added == {}
        assert result.track_count == 0
        assert result.enriched_count == 0
        assert len(result.errors) == 1
        assert "No tracks with database IDs" in result.errors[0]

    @patch("src.application.use_cases.enrich_tracks.MetricsApplicationService")
    async def test_tracks_without_ids_filtered(
        self,
        mock_metrics_service_class,
        use_case,
        external_metadata_config,
        mock_uow,
    ):
        """Test that tracks without database IDs are filtered out."""
        # Arrange
        tracks_without_ids = [
            Track(id=None, title="No ID Track", artists=[Artist(name="Artist")]),
            Track(id=1, title="Has ID Track", artists=[Artist(name="Artist")]),
        ]
        tracklist = TrackList(tracks=tracks_without_ids)
        command = EnrichTracksCommand(
            tracklist=tracklist, enrichment_config=external_metadata_config
        )

        # Mock the MetricsApplicationService instance
        mock_metrics_service = AsyncMock()
        mock_metrics_service.get_external_track_metrics.return_value = {
            "spotify_popularity": {1: 85}
        }
        mock_metrics_service_class.return_value = mock_metrics_service

        # Act
        result = await use_case.execute(command, mock_uow)

        # Assert
        # Should process only the track with ID
        assert result.track_count == 2  # Original count
        # MetricsApplicationService should only receive the track with ID
        mock_metrics_service.get_external_track_metrics.assert_called_once_with(
            track_ids=[1],  # Only the track with ID 1
            connector="spotify",
            metric_names=["spotify_popularity"],
            uow=mock_uow,
            connector_instance=external_metadata_config.connector_instance,
        )

    @patch("src.application.use_cases.enrich_tracks.MetricsApplicationService")
    async def test_enrichment_error_handling(
        self,
        mock_metrics_service_class,
        use_case,
        sample_tracklist,
        external_metadata_config,
        mock_uow,
    ):
        """Test error handling during enrichment."""
        # Arrange
        mock_metrics_service = AsyncMock()
        mock_metrics_service.get_external_track_metrics.side_effect = Exception(
            "API Error"
        )
        mock_metrics_service_class.return_value = mock_metrics_service

        command = EnrichTracksCommand(
            tracklist=sample_tracklist, enrichment_config=external_metadata_config
        )

        # Act
        result = await use_case.execute(command, mock_uow)

        # Assert
        # Should return original tracklist with empty metrics when error occurs
        assert result.metrics_added == {}
        assert result.enriched_count == 0
        assert len(result.errors) == 1
        assert "Track enrichment failed:" in result.errors[0]

    async def test_invalid_enrichment_type(self, use_case, sample_tracklist, mock_uow):
        """Test handling of invalid enrichment type."""
        # Arrange
        invalid_config = EnrichmentConfig(
            enrichment_type="invalid_type",  # type: ignore
        )
        command = EnrichTracksCommand(
            tracklist=sample_tracklist, enrichment_config=invalid_config
        )

        # Act
        result = await use_case.execute(command, mock_uow)

        # Assert
        assert result.enriched_count == 0
        assert len(result.errors) == 1
        assert "Unknown enrichment type" in result.errors[0]


class TestEnrichmentConfig:
    """Test suite for EnrichmentConfig validation."""

    def test_external_metadata_config_validation_success(self):
        """Test valid external metadata configuration."""
        config = EnrichmentConfig(
            enrichment_type="external_metadata",
            connector="spotify",
            connector_instance=Mock(),
            track_metric_names=["spotify_popularity"],
        )
        # Should not raise any validation errors
        assert config.enrichment_type == "external_metadata"

    def test_external_metadata_config_missing_connector(self):
        """Test external metadata config validation with missing connector."""
        with pytest.raises(ValueError, match="Connector must be specified"):
            EnrichmentConfig(
                enrichment_type="external_metadata",
                connector=None,
                connector_instance=Mock(),
                track_metric_names=["spotify_popularity"],
            )

    def test_external_metadata_config_missing_connector_instance(self):
        """Test external metadata config validation with missing connector instance."""
        with pytest.raises(ValueError, match="Connector instance must be provided"):
            EnrichmentConfig(
                enrichment_type="external_metadata",
                connector="spotify",
                connector_instance=None,
                track_metric_names=["spotify_popularity"],
            )

    def test_external_metadata_config_missing_track_metric_names(self):
        """Test external metadata config validation with missing track metric names."""
        with pytest.raises(ValueError, match="Track metric names must be specified"):
            EnrichmentConfig(
                enrichment_type="external_metadata",
                connector="spotify",
                connector_instance=Mock(),
                track_metric_names=[],
            )

    def test_play_history_config_validation_success(self):
        """Test valid play history configuration."""
        config = EnrichmentConfig(
            enrichment_type="play_history", metrics=["total_plays", "last_played_dates"]
        )
        # Should not raise any validation errors
        assert config.enrichment_type == "play_history"

    def test_play_history_config_missing_metrics(self):
        """Test play history config validation with missing metrics."""
        with pytest.raises(ValueError, match="Metrics must be specified"):
            EnrichmentConfig(enrichment_type="play_history", metrics=[])


class TestEnrichTracksCommand:
    """Test suite for EnrichTracksCommand validation."""

    def test_command_validation_success(self):
        """Test valid command creation."""
        tracklist = TrackList(
            tracks=[Track(id=1, title="Test", artists=[Artist(name="Artist")])]
        )
        config = EnrichmentConfig(
            enrichment_type="play_history", metrics=["total_plays"]
        )

        command = EnrichTracksCommand(tracklist=tracklist, enrichment_config=config)
        assert command.tracklist == tracklist
        assert command.enrichment_config == config

    def test_command_validation_empty_tracklist(self):
        """Test command validation with empty tracklist."""
        config = EnrichmentConfig(
            enrichment_type="play_history", metrics=["total_plays"]
        )

        # Empty tracklists should be allowed at command level - use case handles gracefully
        command = EnrichTracksCommand(
            tracklist=TrackList(tracks=[]), enrichment_config=config
        )
        assert command.tracklist.tracks == []
