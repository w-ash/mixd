"""Last.fm business operations - Complex workflows and orchestration.

This module handles complex business logic for Last.fm operations that require
multiple API calls, batch processing, or sophisticated coordination. It uses
the LastFMAPIClient for individual API calls and integrates with shared
services for optimization.

Key components:
- LastFMOperations: High-level business workflows
- Track information retrieval with intelligent matching
- Concurrent processing for multiple track metadata requests
- User library operations (love track, get play history)

The operations layer sits between the thin API client and the connector facade,
providing reusable business logic while maintaining clean separation of concerns.
"""

# pyright: reportAny=false, reportExplicitAny=false

from typing import Any

from attrs import define, field

from src.config import get_logger
from src.domain.entities import PlayRecord, Track, create_lastfm_play_record
from src.infrastructure.connectors.lastfm.client import LastFMAPIClient
from src.infrastructure.connectors.lastfm.conversions import (
    LastFMTrackInfo,
    convert_lastfm_to_domain_track,
)

# Get contextual logger for operations
logger = get_logger(__name__).bind(service="lastfm_operations")


@define(frozen=True, slots=True)
class TrackProcessingResult:
    """Result from processing a track with Last.fm metadata.

    Contains the track ID and typed LastFMTrackInfo from Last.fm API calls.
    Used to maintain type safety in batch processing operations.
    """

    track_id: int
    info: LastFMTrackInfo


@define(slots=True)
class LastFMOperations:
    """Business logic service for complex Last.fm operations."""

    client: LastFMAPIClient = field()

    # Track Information Retrieval

    async def get_track_info_by_mbid(self, mbid: str) -> LastFMTrackInfo:
        """Get comprehensive track information by MusicBrainz ID.

        Uses the same comprehensive parsing approach as artist/title lookups
        to maintain architectural consistency.
        """
        if not mbid:
            return LastFMTrackInfo.empty()

        try:
            result = await self.client.get_track_info_comprehensive_by_mbid(mbid)

            if result:
                return result

            logger.info(
                "Track not in Last.FM catalog",
                mbid=mbid,
                reason="not_found_in_catalog",
            )
            return LastFMTrackInfo.empty()

        except Exception as e:
            # Use error classifier to determine appropriate log level
            from src.infrastructure.connectors.lastfm.error_classifier import (
                LastFMErrorClassifier,
            )

            error_type, error_code, _error_desc = (
                LastFMErrorClassifier().classify_error(e)
            )

            if error_type == "not_found":
                # Track not found is expected behavior, not an error
                logger.info(
                    "Track not in Last.FM catalog",
                    mbid=mbid,
                    error_code=error_code,
                    reason="api_returned_not_found",
                )
            elif error_type in ("temporary", "rate_limit"):
                # Temporary errors are warnings (will be retried)
                logger.warning(
                    f"Temporary error fetching track by MBID from Last.FM: '{mbid}': {e}",
                    error=str(e),
                    error_type=type(e).__name__,
                    classified_as=error_type,
                    error_code=error_code,
                )
            else:
                # Permanent errors or unknown errors are real errors
                logger.error(
                    f"Error fetching track by MBID from Last.FM: '{mbid}': {e}",
                    error=str(e),
                    error_type=type(e).__name__,
                    classified_as=error_type,
                    error_code=error_code,
                )
            return LastFMTrackInfo.empty()

    async def get_track_info(self, artist: str, title: str) -> LastFMTrackInfo:
        """Get comprehensive track information using single optimal API call.

        Uses the comprehensive API call that gets all metadata in one request,
        avoiding multiple API calls that cause performance bottlenecks.
        """
        if not artist or not title:
            return LastFMTrackInfo.empty()

        try:
            result = await self.client.get_track_info_comprehensive(artist, title)

            if result:
                return result

            logger.info(
                "Track not in Last.FM catalog",
                artist=artist,
                title=title,
                reason="not_found_in_catalog",
            )
            return LastFMTrackInfo.empty()

        except Exception as e:
            # Use error classifier to determine appropriate log level
            from src.infrastructure.connectors.lastfm.error_classifier import (
                LastFMErrorClassifier,
            )

            error_type, error_code, _error_desc = (
                LastFMErrorClassifier().classify_error(e)
            )

            if error_type == "not_found":
                # Track not found is expected behavior, not an error
                logger.info(
                    "Track not in Last.FM catalog",
                    artist=artist,
                    title=title,
                    error_code=error_code,
                    reason="api_returned_not_found",
                )
            elif error_type in ("temporary", "rate_limit"):
                # Temporary errors are warnings (will be retried)
                logger.warning(
                    f"Temporary Last.FM error (will retry): {e}",
                    artist=artist,
                    title=title,
                    classified_as=error_type,
                    error_code=error_code,
                )
            else:
                # Permanent errors or unknown errors are real errors
                logger.error(
                    f"Last.FM error: {e}",
                    artist=artist,
                    title=title,
                    classified_as=error_type,
                    error_code=error_code,
                )
            return LastFMTrackInfo.empty()

    async def get_track_info_intelligent(self, track: Track) -> LastFMTrackInfo:
        """Get track info using intelligent matching (MBID first, then artist/title).

        Tries multiple fallback strategies:
        1. MBID lookup (if available)
        2. Artist/title lookup for each artist (multi-artist support)

        Logs each attempt for observability and debugging.
        """
        # Try MBID first if available (check lastfm or musicbrainz metadata)
        mbid = track.get_connector_attribute(
            "lastfm", "lastfm_mbid"
        ) or track.get_connector_attribute("musicbrainz", "musicbrainz_mbid")

        if mbid and isinstance(mbid, str):
            logger.debug(
                "Attempting Last.FM lookup via MBID",
                mbid=mbid,
                track_title=track.title,
                track_id=track.id,
            )
            lastfm_info = await self.get_track_info_by_mbid(mbid)
            if lastfm_info and lastfm_info.lastfm_title:
                logger.debug(
                    "Last.FM MBID lookup successful",
                    mbid=mbid,
                    found_title=lastfm_info.lastfm_title,
                    track_id=track.id,
                )
                return lastfm_info
            logger.debug(
                "Last.FM MBID lookup failed, falling back to artist/title",
                mbid=mbid,
                track_id=track.id,
            )

        # Fallback to artist/title matching - try all artists in order
        if track.artists and track.title:
            for idx, artist in enumerate(track.artists):
                artist_name = artist.name
                logger.debug(
                    "Attempting Last.FM lookup via artist/title",
                    artist=artist_name,
                    title=track.title,
                    artist_index=idx,
                    total_artists=len(track.artists),
                    fallback_from_mbid=bool(mbid),
                    track_id=track.id,
                )
                result = await self.get_track_info(artist_name, track.title)
                if result and result.lastfm_title:
                    logger.debug(
                        "Last.FM artist/title lookup successful",
                        artist=artist_name,
                        artist_index=idx,
                        found_title=result.lastfm_title,
                        track_id=track.id,
                    )
                    return result
                logger.debug(
                    "Last.FM lookup failed with artist, trying next",
                    artist=artist_name,
                    artist_index=idx,
                    remaining_artists=len(track.artists) - idx - 1,
                    track_id=track.id,
                )

            # All artists failed
            logger.warning(
                "Last.FM lookup failed with all artists",
                tried_artists=[a.name for a in track.artists],
                title=track.title,
                track_id=track.id,
            )
            return LastFMTrackInfo.empty()

        logger.warning(
            "Last.FM lookup failed - no MBID or artist/title available",
            track_id=track.id,
        )
        return LastFMTrackInfo.empty()

    # Batch Operations

    async def batch_get_track_info(
        self, tracks: list[Track], **_options: Any
    ) -> dict[int, LastFMTrackInfo]:
        """Fetch track information for multiple tracks using queue-based rate limiting."""
        from src.config import settings
        from src.infrastructure.connectors._shared.rate_limited_batch_processor import (
            RateLimitedBatchProcessor,
        )

        if not tracks:
            return {}

        logger.info(
            f"Starting LastFM batch processing for {len(tracks)} tracks",
            track_count=len(tracks),
        )

        async def process_track(track: Track) -> TrackProcessingResult:
            if track.id is None:
                raise ValueError(
                    f"Track must have an ID for batch processing: {track.title}"
                )

            lastfm_info = await self.get_track_info_intelligent(track)
            return TrackProcessingResult(track.id, lastfm_info)

        # Create rate-limited batch processor with LastFM-specific settings
        lastfm_rate = settings.api.lastfm.rate_limit
        assert lastfm_rate is not None, "Last.fm rate_limit must be configured"
        processor = RateLimitedBatchProcessor(
            rate_per_second=lastfm_rate,
            connector_name="lastfm",
            max_concurrent_tasks=settings.api.lastfm.concurrency,
        )

        # Process batch with queue-based rate limiting
        results: dict[int, LastFMTrackInfo] = {}
        async for _item_id, result in processor.process_batch(tracks, process_track):
            if isinstance(result, TrackProcessingResult) and result.info.lastfm_title:
                results[result.track_id] = result.info

        logger.info(
            "LastFM batch processing completed",
            successful_results=len(results),
            total_tracks=len(tracks),
            success_rate=f"{len(results)}/{len(tracks)}",
        )

        return results

    # User Library Operations

    async def love_track(self, artist: str, title: str) -> bool:
        """Love a track on Last.fm for the authenticated user."""
        return await self.client.love_track(artist, title)

    async def enrich_track_with_lastfm_metadata(self, track: Track) -> Track:
        """Enrich a track with Last.fm metadata using FAST single API call."""
        lastfm_info = await self.get_track_info_intelligent(track)
        return convert_lastfm_to_domain_track(track, lastfm_info)

    # Play History Operations

    async def create_play_record_from_track(
        self, track: Track, timestamp: str | None = None
    ) -> PlayRecord:
        """Create a Last.fm play record from a track."""
        from datetime import UTC, datetime

        # Get Last.fm info to enrich the play record
        lastfm_info = await self.get_track_info_intelligent(track)

        # Parse timestamp or use current time
        import contextlib

        scrobbled_at = datetime.now(UTC)
        if timestamp:
            with contextlib.suppress(ValueError):
                scrobbled_at = datetime.fromisoformat(timestamp)

        # Extract artist and track names
        artist_name = track.artists[0].name if track.artists else "Unknown Artist"
        track_name = track.title or "Unknown Track"

        # Create enriched play record
        return create_lastfm_play_record(
            artist_name=artist_name,
            track_name=track_name,
            scrobbled_at=scrobbled_at,
            album_name=track.album,
            lastfm_track_url=lastfm_info.lastfm_url if lastfm_info else None,
            lastfm_artist_url=lastfm_info.lastfm_artist_url if lastfm_info else None,
            mbid=lastfm_info.lastfm_mbid if lastfm_info else None,
            artist_mbid=lastfm_info.lastfm_artist_mbid if lastfm_info else None,
            loved=lastfm_info.lastfm_user_loved if lastfm_info else False,
        )
