"""Last.fm API client - Pure API wrapper.

This module provides a thin wrapper around the pylast library for Last.fm API
interactions. It handles authentication, individual API calls, and basic error
handling without any business logic or complex orchestration.

Key components:
- LastFMAPIClient: Authenticated client for individual API calls
- Rate limiting with AsyncLimiter
- Basic retry and error handling for API requests
- User-specific API calls (playcount, loved status)

The client is stateless and focuses purely on API communication. Complex
workflows and business logic are handled in separate operation modules.
"""

import asyncio
from datetime import datetime
import time

from aiolimiter import AsyncLimiter
from attrs import define, field
import backoff
import pylast

from src.config import get_logger, resilient_operation, settings

# Get contextual logger for API client operations
logger = get_logger(__name__).bind(service="lastfm_client")


@define(slots=True)
class LastFMAPIClient:
    """Pure Last.fm API client with authentication and rate limiting.

    Provides thin wrapper around pylast with authentication, rate limiting,
    and individual API method calls. No business logic or complex orchestration.
    """

    api_key: str | None = field(default=None)
    api_secret: str | None = field(default=None)
    lastfm_username: str | None = field(default=None)
    client: pylast.LastFMNetwork | None = field(default=None, init=False, repr=False)
    rate_limiter: AsyncLimiter | None = field(default=None, init=False, repr=False)

    def __attrs_post_init__(self) -> None:
        """Initialize Last.fm client with authentication and rate limiting."""
        logger.debug("Initializing Last.fm API client")

        # Set up API credentials from settings or provided values
        self.api_key = self.api_key or settings.credentials.lastfm_key
        self.api_secret = self.api_secret or (
            settings.credentials.lastfm_secret.get_secret_value()
            if settings.credentials.lastfm_secret
            else None
        )
        self.lastfm_username = (
            self.lastfm_username or settings.credentials.lastfm_username
        )

        if not self.api_key:
            logger.warning("Last.fm API key not provided")
            return

        # Initialize pylast client with authentication for write operations
        client_args = {
            "api_key": self.api_key,
            "api_secret": self.api_secret,
            "username": self.lastfm_username,
        }

        # Add password hash for authenticated write operations (like love_track)
        lastfm_password = (
            settings.credentials.lastfm_password.get_secret_value()
            if settings.credentials.lastfm_password
            else None
        )
        if self.api_secret and self.lastfm_username and lastfm_password:
            client_args["password_hash"] = pylast.md5(lastfm_password)
            logger.debug(
                "Last.fm client initialized with authentication for write operations"
            )
        else:
            logger.warning(
                "Last.fm client initialized without password - write operations (like love_track) will fail"
            )

        self.client = pylast.LastFMNetwork(**client_args)

        # Set up rate limiting using API settings
        self.rate_limiter = AsyncLimiter(
            max_rate=settings.api.lastfm_rate_limit,
            time_period=1.0,  # per second
        )

    @property
    def is_configured(self) -> bool:
        """Check if client is properly configured."""
        return self.client is not None and self.rate_limiter is not None

    # Individual Track API Methods

    @resilient_operation("lastfm_get_track_by_mbid")
    @backoff.on_exception(backoff.expo, pylast.WSError, max_tries=3)
    async def get_track_by_mbid(self, mbid: str) -> pylast.Track | None:
        """Get track by MusicBrainz ID."""
        if not self.is_configured or self.client is None or self.rate_limiter is None:
            return None

        # Create contextual logger for this API call
        call_logger = logger.bind(
            operation="get_track_by_mbid", mbid=mbid, api="lastfm"
        )

        call_logger.debug("Starting LastFM API call", method="get_track_by_mbid")
        start_time = time.time()

        try:
            # Rate limit API call
            await self.rate_limiter.acquire()

            # Use standard asyncio threading for concurrent execution
            result = await asyncio.to_thread(self.client.get_track_by_mbid, mbid)

            duration_ms = int((time.time() - start_time) * 1000)
            call_logger.debug(
                "LastFM API call completed",
                duration_ms=duration_ms,
                found=result is not None,
            )

            return result
        except pylast.WSError as e:
            if "not found" in str(e).lower():
                return None
            logger.error(f"Last.fm API error for MBID {mbid}: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to get track by MBID {mbid}: {e}")
            return None

    @resilient_operation("lastfm_get_track")
    @backoff.on_exception(backoff.expo, pylast.WSError, max_tries=3)
    async def get_track(self, artist: str, title: str) -> pylast.Track | None:
        """Get track by artist and title."""
        if not self.is_configured or self.client is None or self.rate_limiter is None:
            return None

        # Create contextual logger for this API call
        call_logger = logger.bind(
            operation="get_track", artist=artist, title=title, api="lastfm"
        )

        call_logger.debug("Starting LastFM API call", method="get_track")
        start_time = time.time()

        try:
            # Rate limit API call
            await self.rate_limiter.acquire()

            # Use standard asyncio threading for concurrent execution
            result = await asyncio.to_thread(self.client.get_track, artist, title)

            duration_ms = int((time.time() - start_time) * 1000)
            call_logger.debug(
                "LastFM API call completed",
                duration_ms=duration_ms,
                found=result is not None,
            )

            return result
        except pylast.WSError as e:
            if "not found" in str(e).lower():
                return None
            logger.error(f"Last.fm API error for '{artist} - {title}': {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to get track '{artist} - {title}': {e}")
            return None

    # User Library API Methods

    @resilient_operation("lastfm_love_track")
    @backoff.on_exception(backoff.expo, pylast.WSError, max_tries=3)
    async def love_track(self, artist: str, title: str) -> bool:
        """Love a track for the authenticated user."""
        if (
            not self.is_configured
            or not self.lastfm_username
            or self.client is None
            or self.rate_limiter is None
        ):
            logger.warning("Cannot love track - no username configured")
            return False

        try:
            # Rate limit API calls
            await self.rate_limiter.acquire()

            # Use standard asyncio threading for concurrent execution
            track = await asyncio.to_thread(self.client.get_track, artist, title)

            # Rate limit the love call as well
            await self.rate_limiter.acquire()
            await asyncio.to_thread(track.love)
            return True
        except pylast.WSError as e:
            logger.error(f"Failed to love track '{artist} - {title}': {e}")
            return False
        except Exception as e:
            logger.error(f"Error loving track '{artist} - {title}': {e}")
            return False

    @resilient_operation("lastfm_get_recent_tracks")
    @backoff.on_exception(backoff.expo, pylast.WSError, max_tries=3)
    async def get_recent_tracks(
        self,
        username: str | None = None,
        limit: int = 200,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
    ) -> list[dict]:
        """Get recent tracks from Last.fm user.getRecentTracks API.

        Returns raw track data that will be converted to PlayRecord objects by the connector.

        Args:
            username: Last.fm username (defaults to configured username)
            limit: Number of tracks to fetch (default 200, max 200)
            from_time: Beginning timestamp (UTC)
            to_time: End timestamp (UTC)

        Returns:
            List of raw track data dictionaries for PlayRecord creation
        """
        if not self.is_configured or self.client is None or self.rate_limiter is None:
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
            # Build parameters for API call
            params = {"limit": limit}

            # Add time range if specified (convert to UNIX timestamps)
            if from_time:
                params["time_from"] = int(from_time.timestamp())
            if to_time:
                params["time_to"] = int(to_time.timestamp())

            # Rate limit API calls
            await self.rate_limiter.acquire()

            # Use standard asyncio threading for concurrent execution
            # Get Last.fm user
            lastfm_user = await asyncio.to_thread(self.client.get_user, user)
            if not lastfm_user:
                logger.error(f"Could not get Last.fm user: {user}")
                return []

            # Rate limit the recent tracks call as well
            await self.rate_limiter.acquire()

            # Get recent tracks using pylast (time-range based fetching)
            recent_tracks = await asyncio.to_thread(
                lastfm_user.get_recent_tracks,
                limit=params["limit"],
                time_from=params.get("time_from"),
                time_to=params.get("time_to"),
            )

            # Convert pylast PlayedTrack objects to raw data for PlayRecord creation
            tracks_data = []
            for played_track in recent_tracks:
                # played_track is a PlayedTrack object with attributes: track, album, playback_date, timestamp
                track = played_track.track
                timestamp_str = played_track.timestamp

                # Skip currently playing tracks (they have no timestamp)
                if not timestamp_str:
                    continue

                # Extract track metadata for PlayRecord creation
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

                # Prepare raw data for PlayRecord creation
                track_data = {
                    "artist_name": artist_name,
                    "track_name": track_name,
                    "album_name": album_name,
                    "timestamp": timestamp_str,  # Keep as string for consistent parsing
                    "lastfm_track_url": track_url,
                    "lastfm_artist_url": artist_url,
                    "lastfm_album_url": album_url,
                    "mbid": track_mbid,
                    "artist_mbid": artist_mbid,
                    "album_mbid": album_mbid,
                    "raw_data": {
                        "track_url": track_url,
                        "artist_url": artist_url,
                        "album_url": album_url,
                    },
                }

                tracks_data.append(track_data)

            logger.info(
                f"Retrieved {len(tracks_data)} recent tracks for user {user}",
                limit=limit,
                from_time=from_time,
                to_time=to_time,
            )

            return tracks_data

        except pylast.WSError as e:
            if "not found" in str(e).lower():
                logger.warning(f"User not found: {user}")
                return []
            logger.error(f"Last.fm API error: {e}")
            raise
        except Exception as e:
            logger.error(f"Error fetching recent tracks: {e}")
            raise

    # Metadata Extraction

    async def extract_track_metadata(self, track: pylast.Track) -> dict:
        """Extract all metadata from a pylast Track object."""
        if not track:
            return {}

        # Metadata extraction is already rate-limited when track was fetched
        # So we can do synchronous extraction here without additional limiting
        try:
            return await asyncio.to_thread(self._extract_metadata_sync, track)
        except Exception as e:
            logger.error(f"Failed to extract track metadata: {e}")
            return {}

    def _extract_metadata_sync(self, track: pylast.Track) -> dict:
        """Synchronously extract metadata fields from pylast track."""
        metadata = {}

        extractors = {
            "title": lambda t: t.get_title(),
            "mbid": lambda t: t.get_mbid(),
            "url": lambda t: t.get_url(),
            "duration": lambda t: t.get_duration(),
            "artist_name": lambda t: t.get_artist() and t.get_artist().get_name(),
            "artist_mbid": lambda t: t.get_artist() and t.get_artist().get_mbid(),
            "artist_url": lambda t: t.get_artist() and t.get_artist().get_url(),
            "album_name": lambda t: t.get_album() and t.get_album().get_name(),
            "album_mbid": lambda t: t.get_album() and t.get_album().get_mbid(),
            "album_url": lambda t: t.get_album() and t.get_album().get_url(),
            "user_playcount": lambda t: int(t.get_userplaycount() or 0)
            if t.username
            else None,
            "user_loved": lambda t: bool(t.get_userloved()) if t.username else False,
            "global_playcount": lambda t: int(t.get_playcount() or 0),
            "listeners": lambda t: int(t.get_listener_count() or 0),
        }

        for field_name, extractor in extractors.items():
            try:
                value = extractor(track)
                if value is not None:
                    metadata[f"lastfm_{field_name}"] = value
            except Exception as e:
                logger.debug(f"Failed to extract {field_name}: {e}")
                continue

        return metadata
