"""Last.fm track resolution service implementing the proven 3-step pattern.

This service resolves Last.fm play data to canonical tracks using:
1. Bulk lookup existing Last.fm connector mappings (fast path)
2. Create new canonical tracks from Last.fm metadata + save connector mapping
3. Enhanced Spotify discovery for new tracks to establish dual connectors

Follows the exact same proven pattern as Spotify resolution but adapted for Last.fm metadata.
"""

from __future__ import annotations

from collections.abc import Callable

from src.config import get_logger
from src.domain.entities import Artist, PlayRecord, Track
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.domain.matching.types import RawProviderMatch
from src.domain.repositories import UnitOfWorkProtocol
from src.infrastructure.connectors.spotify import SpotifyConnector

logger = get_logger(__name__)


class LastfmTrackResolutionService:
    """Resolves Last.fm play data to canonical tracks using proven repository patterns.

    Implements the enhanced 3-step pattern from SCRATCHPAD architecture decision:
    1. Check existing Last.fm connector mappings (bulk lookup)
    2. Create canonical tracks from Last.fm metadata + save connector mapping
    3. Spotify discovery enhancement: attempt to find new tracks on Spotify for dual connectors
    """

    def __init__(self, spotify_connector: SpotifyConnector | None = None):
        """Initialize with optional Spotify connector for discovery enhancement."""
        self.spotify_connector = spotify_connector or SpotifyConnector()
        self.match_evaluation_service = TrackMatchEvaluationService()

    async def resolve_plays_to_canonical_tracks(
        self,
        play_records: list[PlayRecord],
        uow: UnitOfWorkProtocol,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> tuple[list[Track | None], dict[str, int]]:
        """Resolve Last.fm plays to canonical tracks using 3-step enhanced pattern.

        Args:
            play_records: Last.fm play records to resolve
            uow: Unit of work for database operations
            progress_callback: Optional progress reporting

        Returns:
            Tuple of (resolved_tracks, metrics_dict) where:
            - resolved_tracks: List of canonical tracks (same order as input plays)
            - metrics_dict: Resolution statistics
        """
        if not play_records:
            return [], {"existing_mappings": 0, "new_tracks": 0, "spotify_enhanced": 0}

        if progress_callback:
            progress_callback(10, 100, "Extracting unique Last.fm track identifiers...")

        # Step 1: Extract unique Last.fm track identifiers (artist + title combo)
        unique_lastfm_identifiers = self._extract_unique_lastfm_identifiers(
            play_records
        )

        if not unique_lastfm_identifiers:
            logger.warning("No valid Last.fm track identifiers found in play records")
            return [], {"existing_mappings": 0, "new_tracks": 0, "spotify_enhanced": 0}

        if progress_callback:
            progress_callback(
                30, 100, f"Resolving {len(unique_lastfm_identifiers)} unique tracks..."
            )

        # Step 2: Resolve identifiers to canonical tracks using 3-step pattern
        (
            canonical_tracks_map,
            resolution_metrics,
        ) = await self._resolve_lastfm_to_canonical_tracks(
            unique_lastfm_identifiers, uow, progress_callback
        )

        if progress_callback:
            progress_callback(80, 100, "Creating resolved track list...")

        # Step 3: Create resolved tracks list in same order as input plays
        resolved_tracks = []
        for record in play_records:
            identifier = self._create_lastfm_identifier(
                record.artist_name, record.track_name
            )
            canonical_track = canonical_tracks_map.get(identifier)

            if canonical_track:
                resolved_tracks.append(canonical_track)
            else:
                logger.warning(
                    f"Failed to resolve Last.fm track: {record.artist_name} - {record.track_name}"
                )
                # Add None to maintain order - caller can handle unresolved tracks
                resolved_tracks.append(None)

        resolved_count = sum(1 for track in resolved_tracks if track is not None)

        if progress_callback:
            progress_callback(
                100,
                100,
                f"Resolution complete: {resolved_count}/{len(resolved_tracks)} tracks resolved",
            )

        logger.info(
            f"Last.fm resolution complete: {resolved_count}/{len(play_records)} tracks resolved"
        )

        return resolved_tracks, resolution_metrics

    def _extract_unique_lastfm_identifiers(
        self, play_records: list[PlayRecord]
    ) -> set[str]:
        """Extract unique Last.fm track identifiers (artist + title combinations)."""
        if not play_records:
            return set()

        unique_identifiers = set()

        for record in play_records:
            if record.artist_name and record.track_name:
                identifier = self._create_lastfm_identifier(
                    record.artist_name, record.track_name
                )
                unique_identifiers.add(identifier)
            else:
                logger.warning(
                    f"Skipping record with missing artist/track: artist='{record.artist_name}', "
                    f"track='{record.track_name}'"
                )

        logger.debug(
            f"Extracted {len(unique_identifiers)} unique Last.fm identifiers from "
            f"{len(play_records)} play records"
        )

        return unique_identifiers

    def _create_lastfm_identifier(self, artist_name: str, track_name: str) -> str:
        """Create normalized identifier for Last.fm track (artist::title)."""
        # Normalize to handle case variations and extra whitespace
        artist_normalized = artist_name.strip().lower() if artist_name else ""
        track_normalized = track_name.strip().lower() if track_name else ""
        return f"{artist_normalized}::{track_normalized}"

    async def _resolve_lastfm_to_canonical_tracks(
        self,
        lastfm_identifiers: set[str],
        uow: UnitOfWorkProtocol,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> tuple[dict[str, Track], dict[str, int]]:
        """Resolve Last.fm identifiers to canonical tracks using enhanced 3-step pattern.

        Enhanced Pattern:
        1. Check existing Last.fm connector mappings (fast path)
        2. Create new canonical tracks from Last.fm metadata + save connector mapping
        3. Spotify discovery enhancement: attempt Spotify lookup for dual connectors
        """
        if not lastfm_identifiers:
            return {}, {"existing_mappings": 0, "new_tracks": 0, "spotify_enhanced": 0}

        logger.info(
            f"Resolving {len(lastfm_identifiers)} Last.fm identifiers to canonical tracks"
        )

        # Phase 1: Check existing canonical tracks by artist/title matching
        # Note: Last.fm doesn't have stable IDs like Spotify, so we match by metadata
        if progress_callback:
            progress_callback(40, 100, "Checking for existing canonical tracks...")

        existing_canonical_tracks = await self._find_existing_tracks_by_metadata(
            lastfm_identifiers, uow
        )

        # Log each existing track match for visibility
        for identifier, track in existing_canonical_tracks.items():
            artist_name, track_name = self._parse_lastfm_identifier(identifier)
            logger.info(
                f"🔍 Found existing track: {artist_name} - {track_name} (ID: {track.id})"
            )

        if existing_canonical_tracks:
            logger.info(
                f"Found {len(existing_canonical_tracks)} existing canonical tracks"
            )

        # Phase 2: Create missing tracks from Last.fm metadata
        missing_identifiers = {
            identifier
            for identifier in lastfm_identifiers
            if identifier not in existing_canonical_tracks
        }

        new_tracks_count = 0
        spotify_enhanced_count = 0

        if missing_identifiers:
            logger.info(f"Creating {len(missing_identifiers)} new canonical tracks")

            if progress_callback:
                progress_callback(
                    60, 100, f"Creating {len(missing_identifiers)} new tracks..."
                )

            for identifier in missing_identifiers:
                try:
                    artist_name, track_name = self._parse_lastfm_identifier(identifier)

                    # Step 2: Create canonical track from Last.fm metadata
                    canonical_track = await self._create_canonical_track_from_lastfm(
                        artist_name, track_name, uow
                    )

                    if canonical_track:
                        existing_canonical_tracks[identifier] = canonical_track
                        new_tracks_count += 1

                        # Log new track creation
                        logger.info(
                            f"🆕 Created new track: {artist_name} - {track_name} (ID: {canonical_track.id})"
                        )

                        # Step 3: Enhanced Spotify Discovery
                        spotify_found = await self._attempt_spotify_discovery(
                            canonical_track, artist_name, track_name, uow
                        )
                        if spotify_found:
                            spotify_enhanced_count += 1
                            # Log successful Spotify discovery
                            logger.info(
                                f"🎵 Spotify discovery: {artist_name} - {track_name} → enhanced with Spotify mapping"
                            )

                except Exception as e:
                    logger.error(
                        f"Failed to create canonical track for {identifier}: {e}"
                    )
                    continue

        metrics = {
            "existing_mappings": len(existing_canonical_tracks) - new_tracks_count,
            "new_tracks": new_tracks_count,
            "spotify_enhanced": spotify_enhanced_count,
        }

        logger.info(
            f"Last.fm resolution complete: {metrics['existing_mappings']} existing, "
            f"{metrics['new_tracks']} new tracks, {metrics['spotify_enhanced']} Spotify enhanced"
        )

        return existing_canonical_tracks, metrics

    async def _find_existing_tracks_by_metadata(
        self, lastfm_identifiers: set[str], uow: UnitOfWorkProtocol
    ) -> dict[str, Track]:
        """Find existing canonical tracks by Last.fm connector mappings using bulk lookup.

        Uses the same proven pattern as Spotify adapter with bulk connector lookup
        to avoid individual database operations that cause performance issues.

        Args:
            lastfm_identifiers: Set of "artist||title" identifier strings
            uow: Unit of work for database operations

        Returns:
            Dictionary mapping lastfm_identifier -> Track for existing tracks
        """
        if not lastfm_identifiers:
            return {}

        # Create Last.fm connector IDs for bulk lookup
        connections = []
        identifier_to_connector_id = {}

        for identifier in lastfm_identifiers:
            artist_name, track_name = self._parse_lastfm_identifier(identifier)
            connector_id = self._create_lastfm_connector_id(artist_name, track_name)
            connections.append(("lastfm", connector_id))
            identifier_to_connector_id[identifier] = connector_id

        # Bulk lookup existing tracks by Last.fm connector mappings
        logger.info(
            f"🔍 Bulk lookup: checking {len(connections)} Last.fm connector mappings"
        )
        existing_by_connector = (
            await uow.get_connector_repository().find_tracks_by_connectors(connections)
        )

        # Map back from connector IDs to lastfm_identifiers
        existing_canonical_tracks = {}
        for identifier, connector_id in identifier_to_connector_id.items():
            connector_key = ("lastfm", connector_id)
            if connector_key in existing_by_connector:
                existing_canonical_tracks[identifier] = existing_by_connector[
                    connector_key
                ]

        if existing_canonical_tracks:
            logger.info(
                f"🔍 Bulk lookup complete: found {len(existing_canonical_tracks)} existing tracks"
            )

        return existing_canonical_tracks

    async def _create_canonical_track_from_lastfm(
        self, artist_name: str, track_name: str, uow: UnitOfWorkProtocol
    ) -> Track | None:
        """Create new canonical track from Last.fm metadata using repository patterns."""
        try:
            # Create track entity from Last.fm metadata
            track_data = Track(
                title=track_name,
                artists=[Artist(name=artist_name)],
                # Last.fm doesn't provide rich metadata like album, duration, etc.
                # These will be enhanced later if Spotify discovery succeeds
                album=None,
                duration_ms=None,
                isrc=None,
            )

            # Use existing idempotent save_track method (handles deduplication)
            canonical_track = await uow.get_track_repository().save_track(track_data)

            # Create Last.fm connector mapping with domain-calculated confidence
            # Since we created the track FROM Last.fm data, create a perfect match for evaluation
            raw_match = RawProviderMatch(
                connector_id=self._create_lastfm_connector_id(artist_name, track_name),
                match_method="artist_title",  # This is based on artist/title match
                service_data={
                    "title": track_name,
                    "artist": artist_name,
                    "duration_ms": None,  # Last.fm often lacks duration
                    "artist_name": artist_name,
                    "track_name": track_name,
                },
            )

            # Use domain matching to calculate proper confidence
            match_result = self.match_evaluation_service.evaluate_single_match(
                canonical_track, raw_match, "lastfm"
            )

            await uow.get_connector_repository().map_track_to_connector(
                canonical_track,
                "lastfm",
                self._create_lastfm_connector_id(artist_name, track_name),
                "lastfm_import",
                confidence=match_result.confidence,
                metadata={"artist_name": artist_name, "track_name": track_name},
                confidence_evidence=match_result.evidence.as_dict()
                if match_result.evidence
                else None,
                # auto_set_primary=True is the default, so mapping will be set as primary automatically
            )

            logger.debug(
                f"Created canonical track: {artist_name} - {track_name} (confidence: {match_result.confidence})"
            )
            return canonical_track

        except Exception as e:
            logger.error(
                f"Failed to create canonical track for {artist_name} - {track_name}: {e}"
            )
            return None

    async def _attempt_spotify_discovery(
        self,
        canonical_track: Track,
        artist_name: str,
        track_name: str,
        uow: UnitOfWorkProtocol,
    ) -> bool:
        """Enhanced Step 3: Attempt Spotify discovery using domain matching algorithms."""
        try:
            # Search for track on Spotify
            spotify_track = await self.spotify_connector.search_track(
                artist_name, track_name
            )

            if not spotify_track:
                return False

            spotify_id = spotify_track.get("id")

            if not spotify_id:
                return False

            # Create RawProviderMatch structure for domain matching evaluation
            raw_match = RawProviderMatch(
                connector_id=spotify_id,
                match_method="artist_title",  # This is a search-based match
                service_data={
                    "title": spotify_track.get("name", ""),
                    "artist": self._extract_primary_artist(spotify_track),
                    "duration_ms": spotify_track.get("duration_ms"),
                    "id": spotify_id,
                    **spotify_track,  # Include all Spotify metadata
                },
            )

            # Use domain matching service to evaluate the match
            match_result = self.match_evaluation_service.evaluate_single_match(
                canonical_track, raw_match, "spotify"
            )

            # Only create mapping if the match passes business rules
            if match_result.success:
                await uow.get_connector_repository().map_track_to_connector(
                    canonical_track,
                    "spotify",
                    spotify_id,
                    "lastfm_discovery",
                    confidence=match_result.confidence,
                    metadata=spotify_track,
                    confidence_evidence=match_result.evidence.as_dict()
                    if match_result.evidence
                    else None,
                    # auto_set_primary=True is the default, so mapping will be set as primary automatically
                )

                logger.debug(
                    f"Spotify discovery success: {artist_name} - {track_name} -> {spotify_id} "
                    f"(confidence: {match_result.confidence})"
                )
                return True
            else:
                logger.debug(
                    f"Spotify discovery rejected: {artist_name} - {track_name} -> {spotify_id} "
                    f"(confidence: {match_result.confidence} below threshold)"
                )
                return False

        except Exception as e:
            logger.debug(
                f"Spotify discovery failed for {artist_name} - {track_name}: {e}"
            )
            return False

    def _extract_primary_artist(self, spotify_track: dict) -> str:
        """Extract primary artist name from Spotify track data."""
        artists = spotify_track.get("artists", [])
        if artists:
            return artists[0].get("name", "")
        return ""

    def _create_lastfm_connector_id(self, artist_name: str, track_name: str) -> str:
        """Create Last.fm connector ID from artist/title (since Last.fm doesn't have stable IDs)."""
        # Use normalized artist::title as connector ID for Last.fm
        return f"{artist_name.strip()}::{track_name.strip()}"

    def _parse_lastfm_identifier(self, identifier: str) -> tuple[str, str]:
        """Parse Last.fm identifier back to artist and track names."""
        if "::" not in identifier:
            raise ValueError(f"Invalid Last.fm identifier format: {identifier}")

        artist_normalized, track_normalized = identifier.split("::", 1)
        # Note: we lose the original case, but this is acceptable for matching purposes
        return artist_normalized, track_normalized
