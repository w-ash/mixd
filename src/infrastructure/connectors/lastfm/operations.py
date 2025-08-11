"""Last.fm business operations - Complex workflows and orchestration.

This module handles complex business logic for Last.fm operations that require
multiple API calls, batch processing, or sophisticated coordination. It uses
the LastFMAPIClient for individual API calls and integrates with shared
services for optimization.

Key components:
- LastFMOperations: High-level business workflows
- Track information retrieval with intelligent matching
- Batch processing for multiple track metadata requests  
- Integration with APIBatchProcessor for optimization
- User library operations (love track, get play history)

The operations layer sits between the thin API client and the connector facade,
providing reusable business logic while maintaining clean separation of concerns.
"""

from typing import Any

from attrs import define, field

from src.config import get_logger, settings
from src.domain.entities import PlayRecord, Track, create_lastfm_play_record
from src.infrastructure.connectors.lastfm.client import LastFMAPIClient
from src.infrastructure.connectors.lastfm.conversions import (
    LastFMTrackInfo,
    convert_lastfm_to_domain_track,
)

# Get contextual logger for operations
logger = get_logger(__name__).bind(service="lastfm_operations")


@define(slots=True) 
class LastFMOperations:
    """Business logic service for complex Last.fm operations."""

    client: LastFMAPIClient = field()

    @property
    def batch_processor(self):
        """Get pre-configured batch processor for Last.fm operations.
        
        Uses AsyncLimiter for optimal Last.fm performance: creates all tasks immediately
        and lets AsyncLimiter handle rate limiting (4.5/sec) while allowing high concurrency.
        This enables parallel API processing while respecting rate limits.
        """
        from src.infrastructure.connectors._shared.api_batch_processor import (
            APIBatchProcessor,
        )
        
        return APIBatchProcessor(
            batch_size=settings.api.lastfm_batch_size,
            concurrency_limit=settings.api.lastfm_concurrency,
            retry_count=settings.api.lastfm_retry_count,
            retry_base_delay=settings.api.lastfm_retry_base_delay,
            retry_max_delay=settings.api.lastfm_retry_max_delay,
            request_delay=settings.api.default_request_delay,
            rate_limiter=None,  # Rate limiting handled at API client level
            logger_instance=logger,
        )

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
        """Get comprehensive track information by artist and title.""" 
        if not artist or not title:
            return LastFMTrackInfo.empty()

        try:
            track = await self.client.get_track(artist, title)
            if track:
                return LastFMTrackInfo.from_pylast_track_sync(track)
            return LastFMTrackInfo.empty()
        except Exception as e:
            logger.error(f"Failed to get track info for '{artist} - {title}': {e}")
            return LastFMTrackInfo.empty()

    async def get_track_info_intelligent(self, track: Track) -> LastFMTrackInfo:
        """Get track info using intelligent matching (MBID first, then artist/title)."""
        # Try MBID first if available (check lastfm or musicbrainz metadata)
        mbid = track.get_connector_attribute("lastfm", "lastfm_mbid") or track.get_connector_attribute("musicbrainz", "musicbrainz_mbid")
        if mbid:
            lastfm_info = await self.get_track_info_by_mbid(mbid)
            if lastfm_info and lastfm_info.lastfm_title:
                return lastfm_info

        # Fallback to artist/title matching
        if track.artists and track.title:
            artist_name = track.artists[0].name
            return await self.get_track_info(artist_name, track.title)

        return LastFMTrackInfo.empty()

    # Batch Operations

    async def batch_get_track_info(
        self, tracks: list[Track], **_options: Any
    ) -> dict[int, dict[str, Any]]:
        """Fetch track information for multiple tracks using batch processing."""
        if not tracks:
            return {}

        async def process_track(track: Track) -> tuple[int, dict[str, Any]]:
            """Process a single track and return its ID with metadata.""" 
            if track.id is None:
                raise ValueError(f"Track must have an ID for batch processing: {track.title}")
                
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

        # Process using batch processor
        batch_results = await self.batch_processor.process(
            items=tracks,
            process_func=process_track,
            progress_description="Fetching Last.fm track metadata"
        )

        # Convert results to expected format (filter only tracks with metadata)
        results = {track_id: metadata for track_id, metadata in batch_results if metadata}

        logger.info(
            f"Retrieved Last.fm metadata for {len(results)}/{len(tracks)} tracks"
        )
        return results

    # User Library Operations  

    async def love_track(self, artist: str, title: str) -> bool:
        """Love a track on Last.fm for the authenticated user."""
        return await self.client.love_track(artist, title)

    async def enrich_track_with_lastfm_metadata(self, track: Track) -> Track:
        """Enrich a track with Last.fm metadata."""
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
                    scrobbled_at = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
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