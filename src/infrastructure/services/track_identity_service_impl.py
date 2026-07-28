"""Implementation of TrackIdentityServiceProtocol using the new architecture.

This service provides the infrastructure layer implementation of track identity operations
while delegating to the new unambiguous identity pipeline components.
"""

from collections.abc import Callable
from typing import cast, override
from uuid import UUID

from src.config import get_logger
from src.domain.entities import Track
from src.domain.matching.content_digest import service_side
from src.domain.matching.protocols import MatchProvider
from src.domain.matching.types import (
    MatchResult,
    MatchResultsById,
    ProgressCallback,
    ProviderMatchResult,
    RawProviderMatch,
)
from src.domain.repositories.connector import (
    ConnectorMappingSpec,
    ConnectorRepositoryProtocol,
)
from src.domain.repositories.resolution import (
    RejectionCandidate,
    ResolutionRecorderProtocol,
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
    _recorder: ResolutionRecorderProtocol | None
    _provider_factories: dict[str, Callable[[object], MatchProvider]]

    def __init__(
        self,
        track_repo: TrackRepositoryProtocol,
        connector_repo: ConnectorRepositoryProtocol,
        recorder: ResolutionRecorderProtocol | None = None,
    ) -> None:
        """Initialize with repository dependencies.

        ``recorder`` is optional so unit tests that only exercise the provider
        plumbing need not build one; with it absent the negative cache simply
        does not suppress anything, which is the safe direction (a candidate is
        re-proposed rather than a real match silently withheld).
        """
        self.track_repo = track_repo
        self.connector_repo = connector_repo
        self._recorder = recorder

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
        """Persist accepted mappings, minus any pair the user already refused.

        The negative cache is consulted *before* writing, not after: a pair
        that was rejected under this same matcher version and unchanged
        metadata is a decision mixd already has, and re-asserting it would
        re-propose the same wrong match on every import — the amnesia the cache
        exists to end. The mapping write itself emits the ``accepted`` events
        (one seam, in ``map_tracks_to_connectors``).
        """
        accepted = await self._drop_rejected_pairs(matches, connector)
        if not accepted:
            return
        mappings = [
            ConnectorMappingSpec(
                track=mr.track,
                connector=connector,
                connector_id=mr.connector_id,
                match_method=mr.match_method,
                confidence=mr.confidence,
                confidence_evidence=mr.evidence_dict,
            )
            for mr in accepted
        ]
        await self.connector_repo.map_tracks_to_connectors(mappings)

    async def _drop_rejected_pairs(
        self, matches: MatchResultsById, connector: str
    ) -> list[MatchResult]:
        """Filter out matches whose pair is an active cannot-link constraint."""
        candidates = list(matches.values())
        if self._recorder is None or not candidates:
            return candidates

        user_id = candidates[0].track.user_id
        rejections = [
            RejectionCandidate(
                connector=service_side(match.connector_id, match.service_data),
                candidate_track=match.track,
                confidence=match.confidence,
            )
            for match in candidates
        ]
        suppressed = await self._recorder.active_rejections(
            rejections, user_id=user_id, connector_name=connector
        )
        if not suppressed:
            return candidates

        kept = [
            match
            for match in candidates
            if (match.connector_id, match.track.id) not in suppressed
        ]
        logger.info(
            "Suppressed previously-rejected identity pairs",
            connector=connector,
            suppressed=len(candidates) - len(kept),
            proposed=len(candidates),
        )
        return kept
