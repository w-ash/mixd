"""External metadata service interface for Clean Architecture compliance.

This module defines the interface for external metadata enrichment services,
enabling proper dependency injection and testability.
"""

from typing import Any, Protocol

from src.domain.entities.track import TrackList


class ExternalMetadataService(Protocol):
    """Protocol for external metadata enrichment services.

    This protocol defines the interface that infrastructure services
    must implement to provide external metadata enrichment capabilities.
    """

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
        ...
