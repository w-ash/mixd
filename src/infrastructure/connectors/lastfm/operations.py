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

from typing import TYPE_CHECKING, Any

from attrs import define, field

if TYPE_CHECKING:
    from src.domain.entities.track import ConnectorTrack

from src.config import get_logger
from src.domain.entities import PlayRecord, Track, create_lastfm_play_record
from src.infrastructure.connectors.base import BaseAPIConnector
from src.infrastructure.connectors.lastfm.client import LastFMAPIClient
from src.infrastructure.connectors.lastfm.conversions import (
    LastFMTrackInfo,
    convert_lastfm_to_domain_track,
)

# Get contextual logger for operations
logger = get_logger(__name__).bind(service="lastfm_operations")


@define(slots=True)
class LastFMOperations(BaseAPIConnector):
    """Business logic service for complex Last.fm operations."""

    client: LastFMAPIClient = field()
    
    @property
    def connector_name(self) -> str:
        """Service identifier for Last.fm connector."""
        return "lastfm"
    
    def convert_track_to_connector(self, track_data: dict) -> "ConnectorTrack":
        """Convert Last.fm track data to ConnectorTrack domain model."""
        from .conversions import convert_lastfm_track_to_connector
        return convert_lastfm_track_to_connector(track_data)

    # Track Information Retrieval

    async def get_track_info_by_mbid(self, mbid: str) -> LastFMTrackInfo:
        """Get comprehensive track information by MusicBrainz ID."""
        if not mbid:
            return LastFMTrackInfo.empty()

        try:
            track = await self.client.get_track_by_mbid(mbid)
            if track:
                return LastFMTrackInfo.from_pylast_track_sync(track)
            return LastFMTrackInfo.empty()
        except Exception as e:
            logger.error(f"Failed to get track info by MBID {mbid}: {e}")
            return LastFMTrackInfo.empty()


    async def get_track_info(self, artist: str, title: str) -> LastFMTrackInfo:
        """Get comprehensive track information using single optimal API call.
        
        Uses the comprehensive API call that gets all metadata in one request,
        avoiding multiple API calls that cause performance bottlenecks.
        """
        if not artist or not title:
            return LastFMTrackInfo.empty()

        try:
            track_data = await self.client.get_track_info_comprehensive(artist, title)
            
            if track_data:
                return LastFMTrackInfo.from_comprehensive_data(track_data)
                
            return LastFMTrackInfo.empty()
            
        except Exception as e:
            logger.error(
                f"get_track_info failed for '{artist} - {title}': {e}",
                error=str(e),
                error_type=type(e).__name__,
            )
            return LastFMTrackInfo.empty()

    async def get_track_info_intelligent(self, track: Track) -> LastFMTrackInfo:
        """Get track info using intelligent matching (MBID first, then artist/title).
        
        Uses FAST single API call implementation for optimal performance.
        """
        # Try MBID first if available (check lastfm or musicbrainz metadata)
        mbid = track.get_connector_attribute(
            "lastfm", "lastfm_mbid"
        ) or track.get_connector_attribute("musicbrainz", "musicbrainz_mbid")
        
        if mbid:
            lastfm_info = await self.get_track_info_by_mbid(mbid)
            if lastfm_info and lastfm_info.lastfm_title:
                return lastfm_info

        # Fallback to artist/title matching using optimal method
        if track.artists and track.title:
            artist_name = track.artists[0].name
            result = await self.get_track_info(artist_name, track.title)
            return result
        
        return LastFMTrackInfo.empty()

    # Batch Operations

    async def batch_get_track_info(
        self, tracks: list[Track], **_options: Any
    ) -> dict[int, dict[str, Any]]:
        """Fetch track information for multiple tracks using queue-based rate limiting."""
        from src.infrastructure.connectors._shared.rate_limited_batch_processor import RateLimitedBatchProcessor
        from src.config import settings
        
        if not tracks:
            return {}
            
        logger.info(
            f"Starting LastFM batch processing for {len(tracks)} tracks",
            track_count=len(tracks)
        )

        async def process_track(track: Track) -> tuple[int, dict[str, Any]]:
            """Process a single track and return its ID with metadata.
            
            Note: This function will be called with @resilient_operation decorator
            applied by the API client, so retries are handled automatically.
            """
            if track.id is None:
                raise ValueError(
                    f"Track must have an ID for batch processing: {track.title}"
                )

            # Use FAST intelligent track info retrieval (single API call instead of 14)
            lastfm_info = await self.get_track_info_intelligent(track)

            # Convert to metadata dictionary using attrs introspection
            metadata = {}
            if lastfm_info:
                import attrs

                for attrs_field in attrs.fields(type(lastfm_info)):
                    value = getattr(lastfm_info, attrs_field.name)
                    if value is not None:
                        metadata[attrs_field.name] = value

            return track.id, metadata

        # Create rate-limited batch processor with LastFM-specific settings
        processor = RateLimitedBatchProcessor(
            rate_per_second=settings.api.lastfm_rate_limit,
            connector_name=self.connector_name,
            max_concurrent_tasks=settings.api.lastfm_concurrency,
        )

        # Process batch with queue-based rate limiting
        results = {}
        async for item_id, result in processor.process_batch(tracks, process_track):
            if result and isinstance(result, tuple) and len(result) == 2:
                track_id, metadata = result
                if metadata:
                    results[track_id] = metadata
        
        logger.info(
            f"LastFM batch processing completed",
            successful_results=len(results),
            total_tracks=len(tracks),
            success_rate=f"{len(results)}/{len(tracks)}"
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
        scrobbled_at = datetime.now(UTC)
        if timestamp:
            try:
                if isinstance(timestamp, str):
                    scrobbled_at = datetime.fromisoformat(timestamp)
            except ValueError:
                # Keep default current time if parsing fails
                pass

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
