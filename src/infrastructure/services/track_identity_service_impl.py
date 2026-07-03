"""Implementation of TrackIdentityServiceProtocol using the new architecture.

This service provides the infrastructure layer implementation of track identity operations
while delegating to the new unambiguous identity pipeline components.
"""

from collections.abc import Callable
from typing import cast, override
from uuid import UUID

from src.config import get_logger
from src.domain.entities import Track
from src.domain.matching.protocols import MatchProvider
from src.domain.matching.types import (
    MatchResultsById,
    ProgressCallback,
    ProviderMatchResult,
    RawProviderMatch,
)
from src.domain.repositories.connector import (
    ConnectorMappingSpec,
    ConnectorRepositoryProtocol,
)
from src.domain.repositories.track import (
    TrackIdentityServiceProtocol,
    TrackRepositoryProtocol,
)
from src.infrastructure.connectors.lastfm import LastFMProvider
from src.infrastructure.connectors.lastfm.connector import LastFMConnector
from src.infrastructure.connectors.musicbrainz import MusicBrainzProvider
from src.infrastructure.connectors.musicbrainz.connector import MusicBrainzConnector
from src.infrastructure.connectors.spotify import SpotifyProvider
from src.infrastructure.connectors.spotify.client import SpotifyAPIClient

logger = get_logger(__name__)


class TrackIdentityServiceImpl(TrackIdentityServiceProtocol):
    """Infrastructure implementation of track identity service.

    This service implements the TrackIdentityServiceProtocol by coordinating
    with the existing infrastructure while providing a bridge to the new
    architecture components.
    """

    track_repo: TrackRepositoryProtocol
    connector_repo: ConnectorRepositoryProtocol
    _provider_factories: dict[str, Callable[[object], MatchProvider]]

    def __init__(
        self,
        track_repo: TrackRepositoryProtocol,
        connector_repo: ConnectorRepositoryProtocol,
    ) -> None:
        """Initialize with repository dependencies."""
        self.track_repo = track_repo
        self.connector_repo = connector_repo

        # Lambda factories cast the generic connector_instance to each provider's
        # concrete client type, bridging heterogeneous __init__ signatures type-safely.
        self._provider_factories = {
            "spotify": lambda ci: SpotifyProvider(cast(SpotifyAPIClient, ci)),
            "lastfm": lambda ci: LastFMProvider(cast(LastFMConnector, ci)),
            "musicbrainz": lambda ci: MusicBrainzProvider(
                cast(MusicBrainzConnector, ci)
            ),
        }

    @override
    async def get_raw_external_matches(
        self,
        tracks: list[Track],
        connector: str,
        connector_instance: object,
        progress_callback: ProgressCallback | None = None,
        **additional_options: object,
    ) -> dict[UUID, RawProviderMatch]:
        """Get raw matches from external providers.

        This method delegates to the appropriate provider while maintaining
        compatibility with the existing interface.
        """
        if not tracks:
            return {}

        # Get the appropriate provider factory
        provider_factory = self._provider_factories.get(connector)
        if not provider_factory:
            logger.warning(f"No provider available for connector: {connector}")
            return {}

        provider = provider_factory(connector_instance)

        # Fetch raw matches with structured failure handling
        result: ProviderMatchResult = await provider.fetch_raw_matches_for_tracks(
            tracks, progress_callback=progress_callback, **additional_options
        )

        # Log failure summary for observability
        if result.failures:
            failure_count = len(result.failures)
            logger.info(
                f"Provider {connector} reported {failure_count} failures during matching"
            )
            # Individual failures are already logged by providers via log_match_failure()

        return result.matches

    @override
    async def get_existing_identity_mappings(
        self, track_ids: list[UUID], connector: str
    ) -> MatchResultsById:
        """Retrieve existing identity mappings with their stored provenance.

        Each MatchResult carries the mapping row's real confidence and match
        method (v0.8.18 FM1b) — no synthetic constant stands in for stored
        provenance. The full evidence stays in the row; nothing here is
        re-persisted (the pipeline persists only newly accepted matches).
        """
        details = await self.connector_repo.get_primary_mapping_details(
            track_ids, connector
        )

        if not details:
            return {}

        tracks_by_id = await self.track_repo.find_tracks_by_ids(list(details.keys()))

        from src.domain.matching.types import MatchResult

        return {
            track_id: MatchResult(
                track=tracks_by_id[track_id],
                success=True,
                connector_id=detail.connector_id,
                confidence=detail.confidence,
                match_method=detail.match_method,
            )
            for track_id, detail in details.items()
            if track_id in tracks_by_id
        }

    @override
    async def persist_identity_mappings(
        self, matches: MatchResultsById, connector: str
    ) -> None:
        """Save identity mappings to database."""
        mappings = [
            ConnectorMappingSpec(
                track=mr.track,
                connector=connector,
                connector_id=mr.connector_id,
                match_method=mr.match_method,
                confidence=mr.confidence,
                confidence_evidence=mr.evidence_dict,
            )
            for mr in matches.values()
        ]
        await self.connector_repo.map_tracks_to_connectors(mappings)
