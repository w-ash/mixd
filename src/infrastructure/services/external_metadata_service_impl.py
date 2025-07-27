"""Concrete implementation of external metadata service.

This module provides the infrastructure implementation of external metadata
enrichment, wrapping the existing TrackMetadataEnricher functionality.
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
    """Infrastructure implementation of external metadata service.

    This service wraps the existing TrackMetadataEnricher to provide
    Clean Architecture compliance through proper interface abstraction.
    """

    def __init__(
        self,
        track_repo: TrackRepositoryProtocol,
        connector_repo: ConnectorRepositoryProtocol,
        metrics_repo: MetricsRepositoryProtocol,
    ) -> None:
        """Initialize with individual repository interfaces.

        Args:
            track_repo: Core track repository for database operations.
            connector_repo: Connector repository for identity and metadata operations.
            metrics_repo: Metrics repository for storing calculated metrics.
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
        """Fetch external metadata and extract metrics.

        Delegates to the existing TrackMetadataEnricher for the actual work,
        providing a clean interface for the application layer.

        Args:
            tracklist: Tracks to enrich with external metadata.
            connector: Connector name (e.g., 'spotify', 'lastfm').
            connector_instance: Connector implementation instance.
            extractors: Metric extractors for this connector.
            max_age_hours: Override freshness policy.
            **additional_options: Options forwarded to services.

        Returns:
            Tuple of (enriched_tracklist, metrics_dictionary).
        """
        return await self.enricher.enrich_tracks(
            tracklist,
            connector,
            connector_instance,
            extractors,
            max_age_hours,
            **additional_options,
        )
