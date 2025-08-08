"""Last.fm API integration for Narada music metadata.

This module provides a clean interface to the Last.fm API through the pylast library
(https://github.com/pylast/pylast), converting between Last.fm track representations
and domain models. It implements rate limiting, error handling, and batch processing
for efficient data retrieval.

Key components:
- LastFMConnector: Main client with track info retrieval and love operations
- LastFMTrackInfo: Immutable container for Last.fm track metadata
- LastFmMetricResolver: Resolves Last.fm-specific track metrics
- Batch processing utilities: Efficient retrieval of track info for multiple tracks

The module supports:
- Track information retrieval by MBID or artist/title
- User-specific playcount and loved status
- Global playcount and listener metrics
- Loving tracks on Last.fm
"""

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import ClassVar

from aiolimiter import AsyncLimiter
from attrs import define, field
import backoff
import pylast

from src.config import get_logger, resilient_operation, settings
from src.domain.entities import (
    Artist,
    PlayRecord,
    Track,
    create_lastfm_play_record,
)

# API batch processor is imported on-demand in batch_processor property
from src.infrastructure.connectors.base_connector import (
    BaseAPIConnector,
    BaseMetricResolver,
    extract_metric,
    register_metrics,
)
from src.infrastructure.connectors.lastfm_error_classifier import LastFMErrorClassifier
from src.infrastructure.connectors.protocols import ConnectorConfig

# Get contextual logger with service binding
logger = get_logger(__name__)


@define(frozen=True, slots=True)
class LastFMTrackInfo:
    """Complete track information from Last.fm API.

    Immutable container for all track-related data from Last.fm,
    including metadata, artist information, and user-specific metrics.

    Attributes:
        lastfm_title: Track title as known by Last.fm
        lastfm_mbid: MusicBrainz ID for the track if available
        lastfm_url: Last.fm URL for the track
        lastfm_duration: Track duration in milliseconds
        lastfm_artist_name: Artist name as known by Last.fm
        lastfm_artist_mbid: MusicBrainz ID for the artist if available
        lastfm_artist_url: Last.fm URL for the artist
        lastfm_album_name: Album name as known by Last.fm
        lastfm_album_mbid: MusicBrainz ID for the album if available
        lastfm_album_url: Last.fm URL for the album
        lastfm_user_playcount: Number of times the user has played this track
        lastfm_global_playcount: Total play count across all Last.fm users
        lastfm_listeners: Number of unique listeners on Last.fm
        lastfm_user_loved: Whether the user has "loved" this track on Last.fm
    """

    # Basic track info
    lastfm_title: str | None = field(default=None)
    lastfm_mbid: str | None = field(default=None)
    lastfm_url: str | None = field(default=None)
    lastfm_duration: int | None = field(default=None)

    # Artist info
    lastfm_artist_name: str | None = field(default=None)
    lastfm_artist_mbid: str | None = field(default=None)
    lastfm_artist_url: str | None = field(default=None)

    # Album info
    lastfm_album_name: str | None = field(default=None)
    lastfm_album_mbid: str | None = field(default=None)
    lastfm_album_url: str | None = field(default=None)

    # Metrics - None means "unknown/not fetched", 0 means "zero plays"
    lastfm_user_playcount: int | None = field(default=None)
    lastfm_global_playcount: int | None = field(default=None)
    lastfm_listeners: int | None = field(default=None)
    lastfm_user_loved: bool = field(default=False)

    # Field extraction mapping for pylast Track objects
    EXTRACTORS: ClassVar[dict[str, Callable]] = {
        "lastfm_title": lambda t: t.get_title(),
        "lastfm_mbid": lambda t: t.get_mbid(),
        "lastfm_url": lambda t: t.get_url(),
        "lastfm_duration": lambda t: t.get_duration(),
        "lastfm_artist_name": lambda t: t.get_artist() and t.get_artist().get_name(),
        "lastfm_artist_mbid": lambda t: t.get_artist() and t.get_artist().get_mbid(),
        "lastfm_artist_url": lambda t: t.get_artist() and t.get_artist().get_url(),
        "lastfm_album_name": lambda t: t.get_album() and t.get_album().get_name(),
        "lastfm_album_mbid": lambda t: t.get_album() and t.get_album().get_mbid(),
        "lastfm_album_url": lambda t: t.get_album() and t.get_album().get_url(),
        "lastfm_user_playcount": lambda t: int(t.get_userplaycount() or 0)
        if t.username
        else None,
        "lastfm_user_loved": lambda t: bool(t.get_userloved()) if t.username else False,
        "lastfm_global_playcount": lambda t: int(t.get_playcount() or 0),
        "lastfm_listeners": lambda t: int(t.get_listener_count() or 0),
    }

    @classmethod
    def empty(cls) -> "LastFMTrackInfo":
        """Create an empty track info object for tracks not found."""
        return cls()

    @classmethod  
    def from_pylast_track_sync(cls, track: pylast.Track) -> "LastFMTrackInfo":
        """Create LastFMTrackInfo from a pylast Track object (all fields)."""
        info = {}
        extraction_errors = []

        # Extract all fields synchronously - track fetch already rate limited
        for field_name, extractor in cls.EXTRACTORS.items():
                
            try:
                value = extractor(track)
                    
                if value is not None:
                    info[field_name] = value
                    
            except pylast.WSError as e:
                # For metadata extraction, WSErrors are rare but possible
                logger.debug(f"WSError extracting metadata field {field_name}: {e}")
                extraction_errors.append(f"{field_name}: {e}")
                continue
                    
            except (AttributeError, TypeError, ValueError) as e:
                # These might indicate API changes or data format issues
                logger.debug(f"Field format error for {field_name}: {type(e).__name__}({e})")
                extraction_errors.append(f"{field_name}: {type(e).__name__}({e})")
                continue
                
            except Exception as e:
                # Log truly unexpected errors but continue
                logger.warning(
                    f"Unexpected error extracting {field_name}: {type(e).__name__}({e})"
                )
                extraction_errors.append(f"{field_name}: {type(e).__name__}({e})")
                continue

        # Only log extraction issues at debug level if they're just missing fields
        if extraction_errors:
            logger.debug(f"Field extraction errors: {', '.join(extraction_errors)}")

        # Return extracted data even if some fields failed
        if info:
            return cls(**info)

        # Only return empty if no fields could be extracted at all
        logger.warning("No fields could be extracted from pylast Track object")
        return cls.empty()

    def to_domain_track(self) -> Track:
        """Convert Last.fm track info to domain track model."""
        # Create base track with essential fields
        track = Track(
            title=self.lastfm_title or "",
            artists=[Artist(name=self.lastfm_artist_name)]
            if self.lastfm_artist_name
            else [],
            album=self.lastfm_album_name,
            duration_ms=self.lastfm_duration,
        )

        # Add connector IDs
        if self.lastfm_mbid:
            track = track.with_connector_track_id("musicbrainz", self.lastfm_mbid)

        if self.lastfm_url:
            track = track.with_connector_track_id("lastfm", self.lastfm_url)

        # Add all non-None LastFM metadata
        from attrs import asdict

        lastfm_metadata = {
            k: v
            for k, v in asdict(self).items()
            if k.startswith("lastfm_") and v is not None
        }

        if lastfm_metadata:
            track = track.with_connector_metadata("lastfm", lastfm_metadata)

        return track


@define(slots=True)
class LastFMConnector(BaseAPIConnector):
    """Last.fm API connector with domain model conversion.

    Implements the TrackMatcher protocol for identity resolution.
    """

    api_key: str | None = field(default=None)
    api_secret: str | None = field(default=None)
    lastfm_username: str | None = field(default=None)
    client: pylast.LastFMNetwork | None = field(default=None, init=False, repr=False)
    rate_limiter: AsyncLimiter | None = field(default=None, init=False, repr=False)

    @property
    def connector_name(self) -> str:
        """Service identifier for this connector."""
        return "lastfm"

    @property
    def error_classifier(self):
        """Get LastFM-specific error classifier."""
        return LastFMErrorClassifier()

    def get_connector_config(self, key: str, default=None):
        """Load LastFM configuration, extending base class with service-specific settings."""
        # Handle LastFM-specific config keys not in base class
        lastfm_specific = {
            "rate_limit": settings.api.lastfm_rate_limit,
            "retry_constant_delay": settings.api.lastfm_retry_constant_delay,
            "retry_unknown_max": settings.api.lastfm_retry_unknown_max,
            "max_retry_time": settings.api.lastfm_max_retry_time,
            "request_delay": 0.0,  # LastFM uses rate limiter, no artificial delay
        }
        
        key_lower = key.lower()
        if key_lower in lastfm_specific:
            return lastfm_specific[key_lower]
            
        # Delegate to base class for standard config keys
        return super().get_connector_config(key, default)

    @property
    def batch_processor(self):
        """Get pre-configured batch processor with LastFM-specific settings."""
        from src.infrastructure.connectors.api_batch_processor import APIBatchProcessor
        
        return APIBatchProcessor(
            batch_size=settings.api.lastfm_batch_size,
            concurrency_limit=settings.api.lastfm_concurrency,
            retry_count=settings.api.lastfm_retry_count,
            retry_base_delay=settings.api.lastfm_retry_base_delay,
            retry_max_delay=settings.api.lastfm_retry_max_delay,
            request_delay=0.0,  # No artificial delay - rate limiting at API level
            rate_limiter=None,  # Rate limiting handled in _fetch_track
            logger_instance=get_logger(__name__).bind(service=self.connector_name),
        )

    # Constants for API communication
    USER_AGENT: ClassVar[str] = "Narada/0.1.0 (Music Metadata Integration)"

    def __attrs_post_init__(self) -> None:
        """Initialize Last.fm client with API credentials."""
        # Use modern settings system with fallback to passed parameters
        self.api_key = self.api_key or settings.credentials.lastfm_key
        self.api_secret = self.api_secret or settings.credentials.lastfm_secret.get_secret_value()
        self.lastfm_username = self.lastfm_username or settings.credentials.lastfm_username

        # Initialize rate limiter with configurable burst capacity
        # burst=1 means steady drip (no burst), burst>1 allows initial burst
        self.rate_limiter = AsyncLimiter(
            max_rate=settings.api.lastfm_rate_limit_burst,
            time_period=settings.api.lastfm_rate_limit_burst / settings.api.lastfm_rate_limit,
        )

        if not self.api_key or not self.api_secret:
            return

        # For write operations, we need username and password
        lastfm_password = settings.credentials.lastfm_password.get_secret_value()

        if self.lastfm_username and lastfm_password:
            # Full authentication for write operations
            self.client = pylast.LastFMNetwork(
                api_key=str(self.api_key),
                api_secret=str(self.api_secret),
                username=self.lastfm_username,
                password_hash=pylast.md5(lastfm_password),
            )
        else:
            # Read-only client for track info retrieval
            self.client = pylast.LastFMNetwork(
                api_key=str(self.api_key),
                api_secret=str(self.api_secret),
            )

        # Set user agent for API courtesy
        pylast.HEADERS["User-Agent"] = self.USER_AGENT

    async def _fetch_track(
        self,
        mbid: str | None = None,
        artist_name: str | None = None,
        track_title: str | None = None,
    ) -> tuple[str, str, pylast.Track]:
        """Fetch a track from Last.fm using the most appropriate method.
        
        Rate limiting is applied here to control API call start timing while allowing
        high concurrency for calls that are already in flight.
        """
        if not self.client:
            raise ValueError("Last.fm client not initialized")

        # Rate limit the API call start - simple and direct
        if self.rate_limiter:
            async with self.rate_limiter:
                pass  # Wait for permission to start this API call

        # Try MBID lookup first (preferred)
        if mbid:
            track = await asyncio.to_thread(self.client.get_track_by_mbid, mbid)
            return ("mbid", mbid, track)

        # Fall back to artist/title lookup
        if artist_name and track_title:
            track = await asyncio.to_thread(
                self.client.get_track,
                artist_name,
                track_title,
            )
            return ("artist/title", f"{artist_name} - {track_title}", track)

        # No valid lookup parameters
        raise ValueError("Either mbid or (artist_name + track_title) must be provided")

    @resilient_operation("get_lastfm_track_info")
    async def get_lastfm_track_info(
        self,
        artist_name: str | None = None,
        track_title: str | None = None,
        mbid: str | None = None,
        lastfm_username: str | None = None,
    ) -> LastFMTrackInfo:
        """Get comprehensive track information from Last.fm."""
        # Create service-aware retry decorator with LastFM's specific needs
        lastfm_retry = self.create_service_aware_retry(
            backoff_strategy=backoff.constant,
            interval=settings.api.lastfm_retry_constant_delay,  # 8.0s constant delay for rate limits
            max_tries=settings.api.lastfm_retry_count + 1,  # 8 tries + 1 for initial attempt
            max_time=settings.api.lastfm_max_retry_time,  # 180s timeout
            jitter=None,  # No jitter for predictable rate limit recovery
        )
        
        @lastfm_retry
        async def _get_track_info_with_retry() -> LastFMTrackInfo:
            """Internal method with retry logic."""
            if not self.client:
                return LastFMTrackInfo.empty()

            user = lastfm_username or self.lastfm_username

            # Log API call start with lookup method
            lookup_method = "MBID" if mbid else "artist/title"
            lookup_params = (
                {"mbid": mbid} if mbid else {"artist": artist_name, "title": track_title}
            )
            logger.debug(
                "LastFM API call starting",
                method=lookup_method,
                params=lookup_params,
                username=user,
            )

            # Fetch track using appropriate method
            # Rate limiting is handled by the batch processor, so don't double-limit
            _, _, track = await self._fetch_track(mbid, artist_name, track_title)

            # Set username for user-specific data
            track.username = user

            # Convert to domain object - extract all fields synchronously
            # Track fetch is already rate limited, no additional rate limiting needed
            result = LastFMTrackInfo.from_pylast_track_sync(track)
            
            return result
        
        try:
            result = await _get_track_info_with_retry()
            
            # Log successful API call with key metadata
            logger.debug(
                "LastFM API call successful",
                artist=result.lastfm_artist_name if result.lastfm_artist_name else "Unknown",
                title=result.lastfm_title if result.lastfm_title else "Unknown",
                playcount=result.lastfm_user_playcount,
                listeners=result.lastfm_listeners,
            )
            return result

        except ValueError as e:
            logger.error(f"LastFM API call failed - ValueError: {e}")
            raise
        except pylast.WSError as e:
            if "not found" in str(e).lower():
                logger.warning("LastFM API call - track not found", 
                              artist=artist_name, title=track_title, mbid=mbid)
                return LastFMTrackInfo.empty()
            logger.error(f"LastFM API call failed - WSError: {e}")
            raise
        except Exception as e:
            logger.error(f"LastFM API call failed - Unexpected error: {e}")
            return LastFMTrackInfo.empty()

    @resilient_operation("batch_get_track_info")
    async def batch_get_track_info(
        self,
        tracks: list[Track],
        lastfm_username: str | None = None,
        progress_callback: Callable[[str, dict], None] | None = None,
    ) -> dict[int, LastFMTrackInfo]:
        """Batch retrieve Last.fm track information for multiple tracks."""
        if not tracks or not self.client:
            return {}

        user = lastfm_username or self.lastfm_username
        if not user:
            return {}

        async def process_track(track: Track) -> tuple[int, LastFMTrackInfo | None]:
            """Process a single track."""
            if track.id is None:
                logger.warning(f"Track has no ID, skipping: {track.title}")
                return -1, None

            # Try MusicBrainz ID first (highest confidence)
            mbid = track.connector_track_ids.get("musicbrainz")

            try:
                if mbid:
                    result = await self.get_lastfm_track_info(
                        mbid=mbid,
                        lastfm_username=user,
                    )
                    if result and result.lastfm_url:
                        return track.id, result

                # Try each artist sequentially until we find a match
                if track.artists:
                    for i, artist in enumerate(track.artists):
                        result = await self.get_lastfm_track_info(
                            artist_name=artist.name,
                            track_title=track.title,
                            lastfm_username=user,
                        )
                        if result and result.lastfm_url:
                            # Log which artist succeeded for debugging
                            if i > 0:
                                logger.debug(
                                    f"Found LastFM match using fallback artist {i + 1}/{len(track.artists)}: {artist.name}",
                                    track_id=track.id,
                                    track_title=track.title,
                                    artist_used=artist.name,
                                )
                            return track.id, result

                    # No artist worked
                    logger.warning(
                        f"No LastFM match found after trying {len(track.artists)} artists for track {track.id}: {track.title}",
                        track_id=track.id, track_title=track.title, artists=[a.name for a in track.artists]
                    )
                else:
                    logger.warning(
                        f"No lookup method available for track {track.id}: {track.title} (no artists)"
                    )

                return track.id, LastFMTrackInfo.empty()

            except Exception as e:
                logger.error(f"Error processing track {track.id}: {e}")
                return track.id, LastFMTrackInfo.empty()

        # Create a wrapper for progress callback to ensure proper task context
        def wrapped_progress_callback(event_type: str, event_data: dict) -> None:
            if progress_callback:
                # Ensure task_name is properly set for workflow integration
                event_data = event_data.copy()
                if "task_name" not in event_data:
                    event_data["task_name"] = "enrich"
                progress_callback(event_type, event_data)

        # Use the pre-configured batch processor
        batch_results = await self.batch_processor.process(
            tracks,
            process_track,
            progress_callback=wrapped_progress_callback,
            progress_task_name="enrich",
            progress_description="Enriching tracks with Last.fm data",
        )

        # Filter valid results
        return {
            track_id: info
            for track_id, info in batch_results
            if track_id != -1 and info and info.lastfm_url
        }

    @resilient_operation("love_track_on_lastfm")
    @backoff.on_exception(
        backoff.expo,
        (pylast.NetworkError, pylast.MalformedResponseError, pylast.WSError),
        max_tries=settings.api.lastfm_love_track_retry_count,
        jitter=backoff.full_jitter,
    )
    async def love_track(
        self,
        artist_name: str,
        track_title: str,
        username: str | None = None,
    ) -> bool:
        """Love a track on Last.fm.

        Args:
            artist_name: Name of the artist
            track_title: Title of the track
            username: Last.fm username (defaults to configured username)

        Returns:
            True if successful, False otherwise
        """
        if not self.client:
            logger.error("Last.fm client not initialized")
            return False

        # Check if client is authenticated for write operations
        if not hasattr(self.client, "username") or not self.client.username:
            logger.error(
                "Last.fm client not authenticated - set LASTFM_PASSWORD environment variable"
            )
            return False

        # Use provided username or fall back to configured one
        user = username or self.lastfm_username

        if not user:
            logger.error("No Last.fm username provided or configured")
            return False

        try:
            # Get Last.fm user
            lastfm_user = await asyncio.to_thread(self.client.get_user, user)
            if not lastfm_user:
                logger.error(f"Could not get Last.fm user: {user}")
                return False

            # Get track
            lastfm_track = await asyncio.to_thread(
                self.client.get_track,
                artist_name,
                track_title,
            )

            # Love the track
            await asyncio.to_thread(lastfm_track.love)
            logger.info(f"Loved track on Last.fm: {artist_name} - {track_title}")
            return True

        except pylast.WSError as e:
            if "not found" in str(e).lower():
                logger.warning(
                    f"Track not found on Last.fm: {artist_name} - {track_title}"
                )
            else:
                logger.error(f"Last.fm API error: {e}")
            return False
        except Exception as e:
            logger.exception(f"Error loving track on Last.fm: {e}")
            return False

    @resilient_operation("get_recent_tracks")
    @backoff.on_exception(
        backoff.expo,
        (pylast.NetworkError, pylast.MalformedResponseError, pylast.WSError),
        max_tries=settings.api.lastfm_retry_count,
        base=settings.api.lastfm_retry_base_delay,
        max_value=settings.api.lastfm_retry_max_delay,
        jitter=backoff.full_jitter,
    )
    async def get_recent_tracks(
        self,
        username: str | None = None,
        limit: int = 200,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
    ) -> list[PlayRecord]:
        """Get recent tracks from Last.fm user.getRecentTracks API.

        Args:
            username: Last.fm username (defaults to configured username)
            limit: Number of tracks to fetch (default 200, max 200)
            from_time: Beginning timestamp (UTC)
            to_time: End timestamp (UTC)

        Returns:
            List of PlayRecord objects with Last.fm metadata
        """
        if not self.client:
            logger.error("Last.fm client not initialized")
            return []

        user = username or self.lastfm_username
        if not user:
            logger.error("No Last.fm username provided or configured")
            return []

        # Validate limit
        limit = min(
            max(settings.api.lastfm_recent_tracks_min_limit, limit),
            settings.api.lastfm_recent_tracks_max_limit,
        )

        try:
            # Get Last.fm user
            lastfm_user = await asyncio.to_thread(self.client.get_user, user)
            if not lastfm_user:
                logger.error(f"Could not get Last.fm user: {user}")
                return []

            # Build parameters for API call
            params = {
                "limit": limit,
            }

            # Add time range if specified (convert to UNIX timestamps)
            if from_time:
                params["time_from"] = int(from_time.timestamp())
            if to_time:
                params["time_to"] = int(to_time.timestamp())

            # Get recent tracks using pylast (time-range based fetching)
            recent_tracks = await asyncio.to_thread(
                lastfm_user.get_recent_tracks,
                limit=params["limit"],
                time_from=params.get("time_from"),
                time_to=params.get("time_to"),
            )

            # Convert pylast PlayedTrack objects to LastfmPlayRecord
            play_records = []
            for played_track in recent_tracks:
                # played_track is a PlayedTrack object with attributes: track, album, playback_date, timestamp
                track = played_track.track
                timestamp_str = played_track.timestamp

                # Skip currently playing tracks (they have no timestamp)
                if not timestamp_str:
                    continue

                # Parse the timestamp (should be UNIX timestamp as string)
                from datetime import UTC

                try:
                    # Convert string timestamp to datetime
                    timestamp_numeric = int(timestamp_str)
                    scrobbled_at = datetime.fromtimestamp(timestamp_numeric, tz=UTC)
                except (ValueError, TypeError) as e:
                    # Skip tracks with invalid timestamp data
                    logger.warning(
                        f"Skipping track with invalid timestamp: {timestamp_str!r}, "
                        f"track: {track.get_title() if hasattr(track, 'get_title') else track!r}, "
                        f"error: {e}"
                    )
                    continue

                # Extract track metadata
                track_name = (
                    track.get_title() if hasattr(track, "get_title") else str(track)
                )
                artist_name = (
                    track.get_artist().get_name()
                    if hasattr(track, "get_artist") and track.get_artist()
                    else ""
                )
                # Use PlayedTrack.album attribute instead of track.get_album()
                album_name = played_track.album if played_track.album else None

                # Extract URLs and MBIDs
                track_url = track.get_url() if hasattr(track, "get_url") else None
                track_mbid = track.get_mbid() if hasattr(track, "get_mbid") else None

                artist_url = (
                    track.get_artist().get_url()
                    if hasattr(track, "get_artist") and track.get_artist()
                    else None
                )
                artist_mbid = (
                    track.get_artist().get_mbid()
                    if hasattr(track, "get_artist") and track.get_artist()
                    else None
                )

                album_url = (
                    track.get_album().get_url()
                    if hasattr(track, "get_album") and track.get_album()
                    else None
                )
                album_mbid = (
                    track.get_album().get_mbid()
                    if hasattr(track, "get_album") and track.get_album()
                    else None
                )

                # Create unified PlayRecord using factory method
                play_record = create_lastfm_play_record(
                    artist_name=artist_name,
                    track_name=track_name,
                    album_name=album_name,
                    scrobbled_at=scrobbled_at,
                    lastfm_track_url=track_url,
                    lastfm_artist_url=artist_url,
                    lastfm_album_url=album_url,
                    mbid=track_mbid,
                    artist_mbid=artist_mbid,
                    album_mbid=album_mbid,
                    streamable=False,  # Not available in recent tracks API
                    loved=False,  # Not available in recent tracks API
                    api_page=1,  # No pagination support in pylast
                    raw_data={
                        "track_url": track_url,
                        "artist_url": artist_url,
                        "album_url": album_url,
                    },
                )

                play_records.append(play_record)

            logger.info(
                f"Retrieved {len(play_records)} recent tracks for user {user}",
                limit=limit,
                from_time=from_time,
                to_time=to_time,
            )

            return play_records

        except pylast.WSError as e:
            if "not found" in str(e).lower():
                logger.warning(f"User not found: {user}")
                return []
            logger.error(f"Last.fm API error: {e}")
            raise
        except Exception as e:
            logger.error(f"Error fetching recent tracks: {e}")
            raise


@define(frozen=True, slots=True)
class LastFmMetricResolver(BaseMetricResolver):
    """Resolves LastFM metrics from persistence layer."""

    # Map metric names to connector metadata fields
    FIELD_MAP: ClassVar[dict[str, str]] = {
        "lastfm_user_playcount": "lastfm_user_playcount",
        "lastfm_global_playcount": "lastfm_global_playcount",
        "lastfm_listeners": "lastfm_listeners",
    }

    # Connector name for database operations
    CONNECTOR: ClassVar[str] = "lastfm"


def get_connector_config() -> ConnectorConfig:
    """Last.fm connector configuration."""
    return {
        "extractors": {
            "lastfm_user_playcount": lambda obj: extract_metric(
                obj,
                ["lastfm_user_playcount", "userplaycount"],
            ),
            "lastfm_global_playcount": lambda obj: extract_metric(
                obj,
                ["lastfm_global_playcount", "playcount"],
            ),
            "lastfm_listeners": lambda obj: extract_metric(
                obj,
                ["lastfm_listeners", "listeners"],
            ),
        },
        "dependencies": ["musicbrainz"],
        "factory": lambda _params: LastFMConnector(),
        "metrics": LastFmMetricResolver.FIELD_MAP,
    }


# Register all metric resolvers at once
register_metrics(LastFmMetricResolver(), LastFmMetricResolver.FIELD_MAP)
