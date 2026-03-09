"""Tests for enrichment metrics merge behavior.

Verifies that running two enrichers sequentially preserves metrics from both,
instead of the second enricher overwriting the first's metrics dict.
"""

from unittest.mock import AsyncMock, Mock, patch

from src.application.use_cases.enrich_tracks import (
    EnrichmentConfig,
    EnrichTracksCommand,
    EnrichTracksUseCase,
)
from src.domain.entities.track import Artist, Track, TrackList


class TestEnrichmentMetricsMerge:
    """Verifies that sequential enrichers merge metrics instead of overwriting."""

    async def test_external_metadata_merges_with_existing_metrics(self):
        """Second enricher should merge metrics with first enricher's results."""
        # Arrange: tracklist already has lastfm metrics from a previous enricher
        tracks = [
            Track(id=1, title="Song A", artists=[Artist(name="Artist")]),
            Track(id=2, title="Song B", artists=[Artist(name="Artist")]),
        ]
        tracklist = (
            TrackList(tracks=tracks)
            .with_metadata(
                "metrics",
                {
                    "lastfm_user_playcount": {1: 42, 2: 15},
                },
            )
            .with_metadata(
                "fresh_metric_ids",
                {
                    "lastfm_user_playcount": [1, 2],
                },
            )
        )

        # Spotify enricher will add explicit_flag
        mock_metrics_service = AsyncMock()
        mock_metrics_service.get_external_track_metrics.return_value = (
            {"explicit_flag": {1: True, 2: False}},
            {"explicit_flag": {1, 2}},
        )

        mock_uow = Mock()
        mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
        mock_uow.__aexit__ = AsyncMock(return_value=None)

        config = EnrichmentConfig(
            enrichment_type="external_metadata",
            connector="spotify",
            connector_instance=Mock(),
            track_metric_names=["explicit_flag"],
        )
        command = EnrichTracksCommand(tracklist=tracklist, enrichment_config=config)
        use_case = EnrichTracksUseCase(metric_config=Mock())

        # Act
        with patch.object(EnrichTracksUseCase, "metrics_service", mock_metrics_service):
            result = await use_case.execute(command, mock_uow)

        # Assert: both metric sets are present in the final tracklist
        final_metrics = result.enriched_tracklist.metadata.get("metrics", {})
        assert "lastfm_user_playcount" in final_metrics, (
            "Previous enricher's metrics were overwritten!"
        )
        assert "explicit_flag" in final_metrics, (
            "Current enricher's metrics are missing!"
        )
        assert final_metrics["lastfm_user_playcount"] == {1: 42, 2: 15}
        assert final_metrics["explicit_flag"] == {1: True, 2: False}

        # fresh_metric_ids should also be merged
        final_fresh = result.enriched_tracklist.metadata.get("fresh_metric_ids", {})
        assert "lastfm_user_playcount" in final_fresh
        assert "explicit_flag" in final_fresh

    async def test_play_history_already_merges_correctly(self):
        """Play history enrichment already merges — verify it still works."""
        tracks = [
            Track(id=1, title="Song", artists=[Artist(name="Artist")]),
        ]
        tracklist = TrackList(tracks=tracks).with_metadata(
            "metrics",
            {
                "explicit_flag": {1: True},
            },
        )

        mock_plays_repo = AsyncMock()
        mock_plays_repo.get_play_aggregations.return_value = {
            "total_plays": {1: 99},
        }

        mock_uow = Mock()
        mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
        mock_uow.__aexit__ = AsyncMock(return_value=None)
        mock_uow.get_plays_repository.return_value = mock_plays_repo

        config = EnrichmentConfig(
            enrichment_type="play_history",
            metrics=["total_plays"],
        )
        command = EnrichTracksCommand(tracklist=tracklist, enrichment_config=config)
        use_case = EnrichTracksUseCase(metric_config=Mock())

        result = await use_case.execute(command, mock_uow)

        final_metrics = result.enriched_tracklist.metadata.get("metrics", {})
        assert "explicit_flag" in final_metrics, "Pre-existing metrics lost!"
        assert "total_plays" in final_metrics, "Play history metrics missing!"
