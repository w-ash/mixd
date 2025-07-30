"""Service for enriching music tracks with external metadata from streaming platforms.

Fetches missing track metadata (audio features, play counts, etc.) from external
APIs like Spotify and Last.fm, then extracts and stores metrics for analysis.
"""

from typing import Any

from src.application.services.external_metadata_service import ExternalMetadataService
from src.config import get_logger
from src.domain.entities.track import TrackList
from src.domain.repositories.interfaces import (
    ConnectorRepositoryProtocol,
    MetricsRepositoryProtocol,
    TrackRepositoryProtocol,
)

from .track_metrics_manager import TrackMetricsManager

logger = get_logger(__name__)


class ExternalMetadataServiceImpl(ExternalMetadataService):
    """Enriches tracks with external metadata from streaming platforms.

    Coordinates fetching metadata from APIs like Spotify/Last.fm and extracting
    metrics (audio features, popularity scores) for music analysis workflows.
    """

    def __init__(
        self,
        track_repo: TrackRepositoryProtocol,
        connector_repo: ConnectorRepositoryProtocol,
        metrics_repo: MetricsRepositoryProtocol,
    ) -> None:
        """Initialize service with data access repositories.

        Args:
            track_repo: Repository for track database operations.
            connector_repo: Repository for external API identity mapping.
            metrics_repo: Repository for storing calculated metrics.
        """
        self.enricher = TrackMetricsManager(track_repo, connector_repo, metrics_repo)

    async def fetch_and_extract_metadata(
        self,
        tracklist: TrackList,
        connector: str,
        connector_instance: Any,
        extractors: dict[str, Any],
        max_age_hours: float | None = None,
        **additional_options: Any,
    ) -> tuple[TrackList, dict[str, dict[int, Any]]]:
        """Fetch external metadata and extract metrics for music tracks.

        Gets missing metadata from streaming APIs (Spotify audio features, Last.fm
        play counts, etc.) and extracts metrics for analysis. Uses cached data
        when available to minimize API calls.

        Args:
            tracklist: Tracks needing metadata enrichment.
            connector: API source name ('spotify', 'lastfm', etc.).
            connector_instance: Configured API client instance.
            extractors: Functions to extract metrics from API responses.
            max_age_hours: Override default cache expiration policy.
            **additional_options: Connector-specific configuration.

        Returns:
            Tuple of enriched tracks and extracted metrics by track ID.
        """
        return await self.enricher.enrich_tracks(
            tracklist,
            connector,
            connector_instance,
            extractors,
            max_age_hours,
            **additional_options,
        )
