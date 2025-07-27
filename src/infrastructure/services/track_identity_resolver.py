"""Track identity resolution service.

This service handles the mapping between internal track IDs and external connector track IDs.
It focuses solely on identity resolution without any metadata fetching or freshness concerns.
"""

from typing import Any

from src.application.utilities.progress_integration import with_progress
from src.config import get_logger
from src.domain.entities import TrackList
from src.domain.matching.types import MatchResult, MatchResultsById
from src.domain.repositories.interfaces import (
    ConnectorRepositoryProtocol,
    TrackIdentityServiceProtocol,
    TrackRepositoryProtocol,
)

from .matching.providers import create_provider

logger = get_logger(__name__)


class TrackIdentityResolver(TrackIdentityServiceProtocol):
    """Resolves track identities between internal tracks and external connector tracks.

    This service implements the TrackIdentityServiceProtocol and is responsible only
    for identity resolution:
    - Finding existing track-to-connector mappings
    - Creating new mappings when tracks are not yet resolved
    - Managing confidence scores and matching evidence

    It does NOT handle:
    - Metadata fetching or storage
    - Data freshness decisions
    - Service-specific data extraction
    """

    def __init__(
        self,
        track_repo: TrackRepositoryProtocol,
        connector_repo: ConnectorRepositoryProtocol,
    ) -> None:
        """Initialize with individual repository interfaces.

        Args:
            track_repo: Core track repository for database operations.
            connector_repo: Connector repository for cross-service mappings.
        """
        self.track_repo = track_repo
        self.connector_repo = connector_repo

    @with_progress(
        "Resolving track identities",
        estimate_total=lambda track_list: len(track_list.tracks),
    )
    async def resolve_track_identities(
        self,
        track_list: TrackList,
        connector: str,
        connector_instance: Any,
        **additional_options: Any,
    ) -> MatchResultsById:
        """Resolve track identities between internal tracks and external connector tracks.

        Args:
            track_list: Tracks to resolve identities for.
            connector: Target connector name.
            connector_instance: Connector implementation.
            **additional_options: Options forwarded to providers.

        Returns:
            Track IDs mapped to MatchResult objects containing identity mappings.
        """
        # Acknowledge additional options to satisfy linter
        _ = additional_options

        if not track_list.tracks:
            return {}

        # Extract valid tracks with IDs for processing
        valid_tracks = [t for t in track_list.tracks if t.id is not None]
        if not valid_tracks:
            return {}

        # Get all track IDs for database lookup
        track_ids = [t.id for t in valid_tracks if t.id is not None]

        # Use contextual logging for the entire operation
        with logger.contextualize(
            operation="resolve_track_identities",
            connector=connector,
            track_count=len(track_ids),
        ):
            # Step 1: Check database for existing identity mappings
            db_results = await self._get_existing_identity_mappings(
                track_ids, connector
            )

            # Find tracks that need identity resolution
            tracks_to_resolve = [t for t in valid_tracks if t.id not in db_results]

            if not tracks_to_resolve:
                logger.info(
                    f"All {len(db_results)} tracks already have identity mappings"
                )
                return db_results

            # Step 2: Resolve new track identities using provider
            logger.info(
                f"Need to resolve {len(tracks_to_resolve)} track identities for {connector}"
            )

            # Create provider for this connector
            provider = create_provider(connector, connector_instance)

            # Use provider to find matches
            match_results = await provider.find_potential_matches(
                tracks_to_resolve, **additional_options
            )

            # Step 3: Save new identity mappings to database if any found
            if match_results:
                await self._persist_identity_mappings(match_results, connector)

            # Combine results and return
            return {**db_results, **match_results}

    async def _get_existing_identity_mappings(
        self,
        track_ids: list[int],
        connector: str,
    ) -> MatchResultsById:
        """Retrieve existing identity mappings from database.

        Args:
            track_ids: Track IDs to check for existing mappings.
            connector: Target connector name.

        Returns:
            Track IDs mapped to MatchResult objects for existing identity mappings.
        """
        if not track_ids:
            return {}

        with logger.contextualize(
            operation="get_existing_identity_mappings", connector=connector
        ):
            logger.info(
                f"Fetching existing identity mappings for {len(track_ids)} tracks"
            )

            db_mapped_tracks = {}

            # Step 1: Get all mappings in a single batch call
            existing_mappings = await self.connector_repo.get_connector_mappings(
                track_ids, connector
            )

            # Early return if no mappings found
            if not existing_mappings:
                logger.info("No existing identity mappings found")
                return {}

            # Step 2: Create a list of track IDs with mappings and their connector IDs
            mapped_track_ids = []
            track_to_connector_id = {}

            for track_id in track_ids:
                if (
                    track_id in existing_mappings
                    and connector in existing_mappings[track_id]
                ):
                    connector_id = existing_mappings[track_id][connector]
                    mapped_track_ids.append(track_id)
                    track_to_connector_id[track_id] = connector_id

            if not mapped_track_ids:
                logger.info("No valid identity mappings found")
                return {}

            # Step 3: Get all tracks in a single batch call
            tracks_by_id = await self.track_repo.find_tracks_by_ids(mapped_track_ids)

            # Process tracks with existing identity mappings
            for track_id in mapped_track_ids:
                # Skip if track not found
                if track_id not in tracks_by_id:
                    continue

                track = tracks_by_id[track_id]
                connector_id = track_to_connector_id[track_id]

                # Get mapping information (confidence, method, evidence)
                mapping_data = await self.connector_repo.get_mapping_info(
                    track_id=track_id,
                    connector=connector,
                    connector_id=connector_id,
                )

                if not mapping_data:
                    continue

                confidence = mapping_data.get("confidence", 80)
                match_method = mapping_data.get("match_method", "unknown")
                confidence_evidence_dict = mapping_data.get("confidence_evidence", {})

                # Create evidence object if available
                evidence = None
                if confidence_evidence_dict:
                    from src.domain.matching.types import ConfidenceEvidence

                    evidence = ConfidenceEvidence(
                        base_score=confidence_evidence_dict.get("base_score", 0),
                        title_score=confidence_evidence_dict.get("title_score", 0.0),
                        artist_score=confidence_evidence_dict.get("artist_score", 0.0),
                        duration_score=confidence_evidence_dict.get(
                            "duration_score", 0.0
                        ),
                        title_similarity=confidence_evidence_dict.get(
                            "title_similarity", 0.0
                        ),
                        artist_similarity=confidence_evidence_dict.get(
                            "artist_similarity", 0.0
                        ),
                        duration_diff_ms=confidence_evidence_dict.get(
                            "duration_diff_ms", 0
                        ),
                        final_score=confidence_evidence_dict.get("final_score", 0),
                    )

                # Create result with identity mapping only (no service metadata)
                db_mapped_tracks[track_id] = MatchResult(
                    track=track,
                    success=True,
                    connector_id=connector_id,
                    confidence=confidence,
                    match_method=match_method,
                    service_data={},  # No service metadata in identity resolution
                    evidence=evidence,
                )

            logger.info(f"Found {len(db_mapped_tracks)} existing identity mappings")
            return db_mapped_tracks

    async def _persist_identity_mappings(
        self,
        matches: MatchResultsById,
        connector: str,
    ) -> None:
        """Save identity mappings to database.

        Args:
            matches: Track IDs mapped to MatchResult objects.
            connector: Target connector name.
        """
        if not matches:
            return

        with logger.contextualize(
            operation="persist_identity_mappings", connector=connector
        ):
            logger.info(f"Persisting {len(matches)} identity mappings to database")

            # Get all track ids for batch lookup
            track_ids = [
                result.track.id
                for result in matches.values()
                if result.track.id is not None
            ]

            if not track_ids:
                logger.warning("No valid track IDs found in matches")
                return

            # Get all tracks in a single batch operation
            tracks_by_id = await self.track_repo.find_tracks_by_ids(track_ids)

            success_count = 0

            # Process matches and save identity mappings
            for result in matches.values():
                if not result.track.id or result.track.id not in tracks_by_id:
                    continue

                track = tracks_by_id[result.track.id]

                if track.id is None:
                    continue

                try:
                    # Create mapping with confidence and metadata
                    await self.connector_repo.map_track_to_connector(
                        track=track,
                        connector=connector,
                        connector_id=result.connector_id,
                        match_method=result.match_method,
                        confidence=result.confidence,
                        metadata={},  # No metadata stored during identity resolution
                        confidence_evidence=result.evidence.as_dict()
                        if result.evidence
                        else None,
                    )
                    success_count += 1
                except Exception as e:
                    logger.error(f"Error mapping track {track.id}: {e}")

            logger.info(f"Successfully persisted {success_count} identity mappings")
