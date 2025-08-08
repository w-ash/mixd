"""Implementation of TrackIdentityServiceProtocol using the new architecture.

This service provides the infrastructure layer implementation of track identity operations
while delegating to the new unambiguous identity pipeline components.
"""

from typing import Any

from src.config import get_logger
from src.domain.matching.types import (
    MatchResultsById,
    ProviderMatchResult,
    RawProviderMatch,
)
from src.domain.repositories.interfaces import (
    ConnectorRepositoryProtocol,
    TrackIdentityServiceProtocol,
    TrackRepositoryProtocol,
)
from src.infrastructure.matching_providers.lastfm import LastFMProvider
from src.infrastructure.matching_providers.musicbrainz import MusicBrainzProvider
from src.infrastructure.matching_providers.spotify import SpotifyProvider

logger = get_logger(__name__)


class TrackIdentityServiceImpl(TrackIdentityServiceProtocol):
    """Infrastructure implementation of track identity service.

    This service implements the TrackIdentityServiceProtocol by coordinating
    with the existing infrastructure while providing a bridge to the new
    architecture components.
    """

    def __init__(
        self,
        track_repo: TrackRepositoryProtocol,
        connector_repo: ConnectorRepositoryProtocol,
    ) -> None:
        """Initialize with repository dependencies."""
        self.track_repo = track_repo
        self.connector_repo = connector_repo

        # Provider mapping for different connectors
        self._provider_classes = {
            "spotify": SpotifyProvider,
            "lastfm": LastFMProvider,
            "musicbrainz": MusicBrainzProvider,
        }

    async def get_raw_external_matches(
        self,
        tracks: list,
        connector: str,
        connector_instance: Any,
        **additional_options: Any,
    ) -> dict[int, RawProviderMatch]:
        """Get raw matches from external providers.

        This method delegates to the appropriate provider while maintaining
        compatibility with the existing interface.
        """
        if not tracks:
            return {}

        # Get the appropriate provider class
        provider_class = self._provider_classes.get(connector)
        if not provider_class:
            logger.warning(f"No provider available for connector: {connector}")
            return {}

        # Create provider instance
        provider = provider_class(connector_instance)

        # Fetch raw matches with structured failure handling
        result: ProviderMatchResult = await provider.fetch_raw_matches_for_tracks(tracks, **additional_options)
        
        # Log failure summary for observability
        if result.failures:
            failure_count = len(result.failures)
            logger.info(f"Provider {connector} reported {failure_count} failures during matching")
            # Individual failures are already logged by providers via log_match_failure()
            
        # Return only matches for backward compatibility
        # Calling code (MatchAndIdentifyTracksUseCase) only expects successful matches
        return result.matches

    async def _get_existing_identity_mappings(
        self, track_ids: list[int], connector: str
    ) -> MatchResultsById:
        """Retrieve existing identity mappings from database.

        This method uses the existing connector repository to get mappings
        and loads the actual Track objects.
        """
        # Use existing connector repository functionality
        mappings = await self.connector_repo.get_connector_mappings(
            track_ids, connector
        )

        # Load actual Track objects for the mapped track IDs
        mapped_track_ids = [
            track_id
            for track_id, mapping_data in mappings.items()
            if connector in mapping_data
        ]

        if not mapped_track_ids:
            return {}

        # Load Track objects from database
        tracks_by_id = await self.track_repo.find_tracks_by_ids(mapped_track_ids)

        # Convert to MatchResultsById format with actual Track objects
        result: MatchResultsById = {}
        for track_id, mapping_data in mappings.items():
            if connector in mapping_data and track_id in tracks_by_id:
                from src.domain.matching.types import MatchResult

                result[track_id] = MatchResult(
                    track=tracks_by_id[track_id],  # Use actual Track object
                    success=True,
                    connector_id=mapping_data.get(connector, ""),
                    confidence=90,  # Default confidence for existing mappings
                    match_method="existing_mapping",
                )
        return result

    async def _persist_identity_mappings(
        self, matches: MatchResultsById, connector: str
    ) -> None:
        """Save identity mappings to database.

        This method uses the existing repository infrastructure to persist mappings.
        """
        for match_result in matches.values():
            if hasattr(match_result, "track") and hasattr(match_result.track, "id"):
                # Use existing repository method to save mapping
                await self.connector_repo.map_track_to_connector(
                    track=match_result.track,
                    connector=connector,
                    connector_id=match_result.connector_id,
                    match_method=match_result.match_method,
                    confidence=match_result.confidence,
                    confidence_evidence=match_result.evidence.as_dict()
                    if match_result.evidence
                    else None,
                )
