"""Last.fm track resolution service.

Resolves Last.fm play data to canonical tracks by:
1. Extracting unique artist::title identifiers from play records
2. Delegating to LastfmInwardResolver for bulk lookup + creation
3. Mapping resolved tracks back to the original play record order
"""

from __future__ import annotations

from collections.abc import Callable

from src.config import get_logger
from src.domain.entities import PlayRecord, Track
from src.domain.matching.protocols import CrossDiscoveryProvider
from src.domain.repositories import UnitOfWorkProtocol
from src.infrastructure.connectors.lastfm.client import LastFMAPIClient
from src.infrastructure.connectors.lastfm.identifiers import make_lastfm_identifier
from src.infrastructure.connectors.lastfm.inward_resolver import (
    LastfmInwardResolver,
)

logger = get_logger(__name__)


class LastfmTrackResolutionService:
    """Resolves Last.fm play data to canonical tracks.

    Thin orchestration layer that:
    - Extracts unique identifiers from play records
    - Delegates resolution to LastfmInwardResolver (shared base class)
    - Maps resolved tracks back to input order
    """

    lastfm_client: LastFMAPIClient
    _inward_resolver: LastfmInwardResolver

    def __init__(
        self,
        cross_discovery: CrossDiscoveryProvider | None = None,
        lastfm_client: LastFMAPIClient | None = None,
    ):
        self.lastfm_client = lastfm_client or LastFMAPIClient()
        self._inward_resolver = LastfmInwardResolver(
            lastfm_client=self.lastfm_client,
            cross_discovery=cross_discovery,
        )

    async def resolve_plays_to_canonical_tracks(
        self,
        play_records: list[PlayRecord],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> tuple[list[Track | None], dict[str, int]]:
        """Resolve Last.fm plays to canonical tracks.

        Returns:
            Tuple of (resolved_tracks in input order, metrics dict).
        """
        if not play_records:
            return [], {"existing_mappings": 0, "new_tracks": 0, "spotify_enhanced": 0}

        if progress_callback:
            progress_callback(10, 100, "Extracting unique Last.fm track identifiers...")

        # Step 1: Extract unique identifiers
        unique_identifiers = self._extract_unique_lastfm_identifiers(play_records)

        if not unique_identifiers:
            logger.warning("No valid Last.fm track identifiers found in play records")
            return [], {"existing_mappings": 0, "new_tracks": 0, "spotify_enhanced": 0}

        if progress_callback:
            progress_callback(
                30, 100, f"Resolving {len(unique_identifiers)} unique tracks..."
            )

        # Step 2: Delegate to inward resolver
        (
            canonical_tracks_map,
            resolution_metrics,
        ) = await self._inward_resolver.resolve_to_canonical_tracks(
            list(unique_identifiers), uow, user_id=user_id
        )

        if progress_callback:
            progress_callback(80, 100, "Creating resolved track list...")

        # Step 3: Map back to input order
        resolved_tracks: list[Track | None] = []
        for record in play_records:
            identifier = make_lastfm_identifier(record.artist_name, record.track_name)
            canonical_track = canonical_tracks_map.get(identifier)

            if canonical_track:
                resolved_tracks.append(canonical_track)
            else:
                logger.warning(
                    f"Failed to resolve Last.fm track: {record.artist_name} - {record.track_name}"
                )
                resolved_tracks.append(None)

        resolved_count = sum(1 for t in resolved_tracks if t is not None)

        if progress_callback:
            progress_callback(
                100,
                100,
                f"Resolution complete: {resolved_count}/{len(resolved_tracks)} tracks resolved",
            )

        logger.info(
            f"Last.fm resolution complete: {resolved_count}/{len(play_records)} tracks resolved"
        )

        metrics = {
            "existing_mappings": resolution_metrics.existing,
            "new_tracks": resolution_metrics.created,
            "spotify_enhanced": 0,  # Tracked internally by inward resolver
        }

        return resolved_tracks, metrics

    def _extract_unique_lastfm_identifiers(
        self, play_records: list[PlayRecord]
    ) -> set[str]:
        """Extract unique Last.fm track identifiers (artist + title combinations)."""
        unique_identifiers: set[str] = set()

        for record in play_records:
            if record.artist_name and record.track_name:
                identifier = make_lastfm_identifier(
                    record.artist_name, record.track_name
                )
                unique_identifiers.add(identifier)
            else:
                logger.warning(
                    f"Skipping record with missing artist/track: artist='{record.artist_name}', track='{record.track_name}'"
                )

        logger.debug(
            f"Extracted {len(unique_identifiers)} unique Last.fm identifiers from {len(play_records)} play records"
        )

        return unique_identifiers
