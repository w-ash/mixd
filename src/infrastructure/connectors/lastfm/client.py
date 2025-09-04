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
import contextvars
from datetime import datetime
import time
from typing import Any

from attrs import define, field
import backoff
import pylast

from src.config import get_logger, resilient_operation, settings
from src.infrastructure.connectors._shared.request_start_gate import RequestStartGate

# Get contextual logger for API client operations
logger = get_logger(__name__).bind(service="lastfm_client")

# Context variable to get call ID from API batch processor
call_id_context: contextvars.ContextVar[str] = contextvars.ContextVar(
    "call_id", default=""
)


@define(slots=True)
class LastFMAPIClient:
    """Pure Last.fm API client with authentication.

    Provides thin wrapper around pylast with authentication and individual API
    method calls. No business logic, rate limiting, or complex orchestration.
    Rate limiting is handled at the batch processor level for optimal performance.
    
    Uses standard asyncio.to_thread() for concurrent I/O operations with the
    application's configured default executor.
    """

    api_key: str | None = field(default=None)
    api_secret: str | None = field(default=None)
    lastfm_username: str | None = field(default=None)
    request_gate: RequestStartGate | None = field(default=None)
    client: pylast.LastFMNetwork | None = field(default=None, init=False, repr=False)
    lastfm_password_hash: str | None = field(default=None, init=False, repr=False)
    _request_gate: RequestStartGate | None = field(default=None, init=False, repr=False)

    def __attrs_post_init__(self) -> None:
        """Initialize Last.fm client with authentication."""
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
            self.lastfm_password_hash = pylast.md5(lastfm_password)
            client_args["password_hash"] = self.lastfm_password_hash
            logger.debug(
                "Last.fm client initialized with authentication for write operations"
            )
        else:
            logger.warning(
                "Last.fm client initialized without password - write operations (like love_track) will fail"
            )

        self.client = pylast.LastFMNetwork(**client_args)

        # Set up request gate for rate limiting
        calculated_delay = 1.0 / settings.api.lastfm_rate_limit
        logger.debug(
            "RequestStartGate configuration",
            rate_limit_setting=settings.api.lastfm_rate_limit,
            calculated_delay_seconds=calculated_delay,
            calculated_delay_ms=round(calculated_delay * 1000, 3),
            expected_requests_per_second=settings.api.lastfm_rate_limit
        )
        
        self._request_gate = (
            self.request_gate
            or RequestStartGate(delay=calculated_delay)
        )

    @property
    def is_configured(self) -> bool:
        """Check if client is properly configured."""
        return self.client is not None

    # Individual Track API Methods

    @resilient_operation("lastfm_get_track_by_mbid")
    @backoff.on_exception(backoff.expo, pylast.WSError, max_tries=3)
    async def get_track_by_mbid(self, mbid: str) -> pylast.Track | None:
        """Get track by MusicBrainz ID."""
        if not self.is_configured or self.client is None:
            return None

        # Get call_id from context for detailed tracing
        call_id = call_id_context.get("") or f"lastfm_{int(time.time()*1000)%1000000}"
        call_id_context.set(call_id)

        # Rate limiting now handled by RateLimitedBatchProcessor at higher level

        # Create contextual logger for this API call
        call_logger = logger.bind(
            operation="get_track_by_mbid", mbid=mbid, api="lastfm"
        )

        call_logger.debug("Starting Last.fm MBID lookup")
        start_time = time.time()

        try:
            # Simple function to call pylast
            def get_track_by_mbid_blocking():
                """Get track by MBID using shared pylast client."""
                if not self.client:
                    return None
                return self.client.get_track_by_mbid(mbid)

            # Use standard asyncio.to_thread() with configured default executor
            result = await asyncio.wait_for(
                asyncio.to_thread(get_track_by_mbid_blocking),
                timeout=settings.api.lastfm_request_timeout,
            )

            duration_ms = int((time.time() - start_time) * 1000)
            call_logger.debug(
                "Last.fm MBID lookup completed",
                duration_ms=duration_ms,
                found=result is not None,
            )

            return result
        except TimeoutError:
            duration_ms = int((time.time() - start_time) * 1000)
            call_logger.warning(f"Last.fm API timeout after {duration_ms}ms")
            return None
        except pylast.WSError as e:
            if "not found" in str(e).lower():
                return None
            call_logger.error(f"Last.fm API error: {e}")
            raise
        except Exception as e:
            call_logger.error(f"Failed to get track by MBID: {e}")
            return None

    @resilient_operation("lastfm_get_track")
    @backoff.on_exception(backoff.expo, pylast.WSError, max_tries=3)
    async def get_track(self, artist: str, title: str) -> pylast.Track | None:
        """Get track by artist and title."""
        if not self.is_configured or self.client is None:
            return None

        # Get call_id from context for detailed tracing
        call_id = call_id_context.get("") or f"lastfm_{int(time.time()*1000)%1000000}"
        call_id_context.set(call_id)

        # Rate limiting now handled by RateLimitedBatchProcessor at higher level

        # Create contextual logger for this API call with call_id
        call_logger = logger.bind(
            call_id=call_id,
            operation="get_track",
            artist=artist,
            title=title,
            api="lastfm",
        )

        start_time = time.time()
        call_logger.debug("Starting Last.fm track lookup")

        try:
            # Create fresh pylast client per request for thread safety
            def get_track_blocking():
                """Get track using fresh pylast client to avoid concurrent access issues."""
                import threading
                thread_start = time.time()
                thread_id = threading.get_ident()
                thread_name = threading.current_thread().name
                
                call_logger.debug(
                    "LastFM API call starting in thread",
                    thread_id=thread_id,
                    thread_name=thread_name,
                    thread_start_time=thread_start,
                )
                
                client_args = {
                    "api_key": settings.credentials.lastfm_key,
                    "api_secret": settings.credentials.lastfm_secret.get_secret_value(),
                }
                fresh_client = pylast.LastFMNetwork(**client_args)
                
                api_call_start = time.time()
                result = fresh_client.get_track(artist, title)
                api_call_end = time.time()
                
                call_logger.debug(
                    "LastFM API call completed in thread",
                    thread_id=thread_id,
                    thread_name=thread_name,
                    api_call_duration_ms=round((api_call_end - api_call_start) * 1000, 1),
                    total_thread_duration_ms=round((api_call_end - thread_start) * 1000, 1),
                )
                
                return result

            # Use standard asyncio.to_thread() with configured default executor
            to_thread_start = time.time()
            call_logger.debug(
                "Submitting to thread pool",
                to_thread_start_time=to_thread_start,
            )
            
            result = await asyncio.wait_for(
                asyncio.to_thread(get_track_blocking),
                timeout=settings.api.lastfm_request_timeout,
            )
            
            to_thread_end = time.time()
            call_logger.debug(
                "Thread pool execution completed",
                to_thread_duration_ms=round((to_thread_end - to_thread_start) * 1000, 1),
            )

            duration_ms = int((time.time() - start_time) * 1000)
            call_logger.debug(
                "Last.fm track lookup completed",
                duration_ms=duration_ms,
                found=result is not None,
            )

            return result
        except TimeoutError:
            duration_ms = int((time.time() - start_time) * 1000)
            call_logger.warning(
                "Last.fm API timeout",
                duration_ms=duration_ms,
                timeout_limit_ms=settings.api.lastfm_request_timeout * 1000,
            )
            return None
        except pylast.WSError as e:
            if "not found" in str(e).lower():
                call_logger.debug("LastFM: Track not found", error=str(e))
                return None
            call_logger.error("LastFM: WSError", error=str(e), error_type="WSError")
            raise
        except Exception as e:
            call_logger.error(
                "LastFM: Unexpected error", error=str(e), error_type=type(e).__name__
            )
            return None

    @resilient_operation("lastfm_get_track_info_comprehensive")
    @backoff.on_exception(backoff.expo, pylast.WSError, max_tries=3)
    async def get_track_info_comprehensive(self, artist: str, title: str) -> dict[str, Any] | None:
        """Get comprehensive track info in a single API call using raw track.getInfo response.
        
        This method makes ONE API call to track.getInfo and extracts all metadata from the 
        raw response, avoiding the 14 individual API calls that pylast's lazy methods make.
        
        Returns:
            Dict with all track metadata fields, or None if track not found
        """
        if not self.is_configured or self.client is None:
            return None

        # Get call_id from context for detailed tracing
        call_id = call_id_context.get("") or f"lastfm_{int(time.time()*1000)%1000000}"
        call_id_context.set(call_id)

        # Create contextual logger for this API call with call_id
        call_logger = logger.bind(
            call_id=call_id,
            operation="get_track_info_comprehensive",
            artist=artist,
            title=title,
            api="lastfm",
        )

        start_time = time.time()
        call_logger.debug("Starting comprehensive Last.fm track info lookup")

        try:
            def get_track_info_raw():
                """Make direct track.getInfo API call and parse raw response."""
                import threading
                thread_start = time.time()
                thread_id = threading.get_ident()
                thread_name = threading.current_thread().name
                
                call_logger.debug(
                    "Comprehensive track info API call starting in thread",
                    thread_id=thread_id,
                    thread_name=thread_name,
                    thread_start_time=thread_start,
                )
                
                # Create fresh pylast client for thread safety
                client_args = {
                    "api_key": settings.credentials.lastfm_key,
                    "api_secret": settings.credentials.lastfm_secret.get_secret_value(),
                }
                if self.lastfm_username:
                    client_args["username"] = self.lastfm_username
                    if self.lastfm_password_hash:
                        client_args["password_hash"] = self.lastfm_password_hash
                
                fresh_client = pylast.LastFMNetwork(**client_args)
                
                # Make single comprehensive API call to track.getInfo
                api_call_start = time.time()
                track = fresh_client.get_track(artist, title)
                
                # Access raw response data instead of making additional API calls
                # This uses pylast's internal _request method to get comprehensive data
                raw_data = track._request(track.ws_prefix + ".getInfo", cacheable=True)
                api_call_end = time.time()
                
                call_logger.debug(
                    "Comprehensive track info API call completed",
                    thread_id=thread_id,
                    api_call_duration_ms=round((api_call_end - api_call_start) * 1000, 1),
                    total_thread_duration_ms=round((api_call_end - thread_start) * 1000, 1),
                    raw_data_available=raw_data is not None,
                    raw_data_type=type(raw_data).__name__,
                    raw_data_content=str(raw_data)[:200] + "..." if raw_data else "None",
                )
                
                if not raw_data:
                    return None
                
                # Parse all metadata from single response
                metadata_parsing_start = time.time()
                
                # Extract track info from XML response (minidom.Document)
                track_info = {}
                
                # Handle minidom.Document response
                try:
                    if hasattr(raw_data, 'getElementsByTagName'):  # minidom.Document response
                        track_elements = raw_data.getElementsByTagName('track')
                        if track_elements:
                            track_element = track_elements[0]
                            
                            # Basic track fields using minidom methods
                            track_info['lastfm_title'] = self._extract_minidom_text(track_element, 'name')
                            track_info['lastfm_mbid'] = self._extract_minidom_text(track_element, 'mbid')
                            track_info['lastfm_url'] = self._extract_minidom_text(track_element, 'url')
                            track_info['lastfm_duration'] = self._extract_minidom_int(track_element, 'duration')
                            track_info['lastfm_global_playcount'] = self._extract_minidom_int(track_element, 'playcount')
                            track_info['lastfm_listeners'] = self._extract_minidom_int(track_element, 'listeners')
                            
                            # Artist info
                            artist_elements = track_element.getElementsByTagName('artist')
                            if artist_elements:
                                artist_element = artist_elements[0]
                                track_info['lastfm_artist_name'] = self._extract_minidom_text(artist_element, 'name')
                                track_info['lastfm_artist_mbid'] = self._extract_minidom_text(artist_element, 'mbid')
                                track_info['lastfm_artist_url'] = self._extract_minidom_text(artist_element, 'url')
                            
                            # Album info
                            album_elements = track_element.getElementsByTagName('album')
                            if album_elements:
                                album_element = album_elements[0]
                                track_info['lastfm_album_name'] = self._extract_minidom_text(album_element, 'title')
                                track_info['lastfm_album_mbid'] = self._extract_minidom_text(album_element, 'mbid')
                                track_info['lastfm_album_url'] = self._extract_minidom_text(album_element, 'url')
                            
                            # User-specific data (if username provided)
                            if self.lastfm_username:
                                track_info['lastfm_user_playcount'] = self._extract_minidom_int(track_element, 'userplaycount')
                                userloved_elements = track_element.getElementsByTagName('userloved')
                                if userloved_elements:
                                    userloved_text = self._extract_minidom_text(track_element, 'userloved')
                                    track_info['lastfm_user_loved'] = userloved_text == '1'
                    
                    metadata_parsing_end = time.time()
                    call_logger.debug(
                        "Metadata parsing completed",
                        parsing_duration_ms=round((metadata_parsing_end - metadata_parsing_start) * 1000, 1),
                        fields_extracted=len([k for k, v in track_info.items() if v is not None]),
                    )
                    
                    return track_info
                    
                except Exception as parse_error:
                    call_logger.error(
                        "Error parsing track info response",
                        error=str(parse_error),
                        error_type=type(parse_error).__name__,
                    )
                    return None

            # Use standard asyncio.to_thread() with configured default executor
            result = await asyncio.wait_for(
                asyncio.to_thread(get_track_info_raw),
                timeout=settings.api.lastfm_request_timeout,
            )

            duration_ms = int((time.time() - start_time) * 1000)
            call_logger.info(
                "Comprehensive track info lookup completed",
                duration_ms=duration_ms,
                found=result is not None,
                fields_count=len(result) if result else 0,
            )

            return result
            
        except TimeoutError:
            duration_ms = int((time.time() - start_time) * 1000)
            call_logger.warning(
                "Comprehensive track info timeout",
                duration_ms=duration_ms,
                timeout_seconds=settings.api.lastfm_request_timeout,
            )
            return None
            
        except pylast.WSError as e:
            if "not found" in str(e).lower():
                call_logger.debug("Track not found on Last.fm")
                return None
            call_logger.error(f"Last.fm API error: {e}")
            raise
            
        except Exception as e:
            call_logger.error(f"Failed to get comprehensive track info: {e}")
            return None

    def _extract_xml_text(self, element, tag_name: str) -> str | None:
        """Extract text content from XML element, return None if not found or empty."""
        child = element.find(tag_name)
        if child is not None and child.text and child.text.strip():
            return child.text.strip()
        return None
    
    def _extract_xml_int(self, element, tag_name: str) -> int | None:
        """Extract integer content from XML element, return None if not found or invalid."""
        text = self._extract_xml_text(element, tag_name)
        if text and text.isdigit():
            return int(text)
        return None

    def _extract_minidom_text(self, element, tag_name: str) -> str | None:
        """Extract text content from minidom element, return None if not found or empty."""
        child_elements = element.getElementsByTagName(tag_name)
        if child_elements:
            child = child_elements[0]
            if child.firstChild and child.firstChild.nodeValue:
                text = child.firstChild.nodeValue.strip()
                return text if text else None
        return None
    
    def _extract_minidom_int(self, element, tag_name: str) -> int | None:
        """Extract integer content from minidom element, return None if not found or invalid."""
        text = self._extract_minidom_text(element, tag_name)
        if text and text.isdigit():
            return int(text)
        return None

    # User Library API Methods

    @resilient_operation("lastfm_love_track")
    @backoff.on_exception(backoff.expo, pylast.WSError, max_tries=3)
    async def love_track(self, artist: str, title: str) -> bool:
        """Love a track for the authenticated user."""
        if not self.is_configured or not self.lastfm_username or self.client is None:
            logger.warning("Cannot love track - no username configured")
            return False

        try:
            # Use asyncio.to_thread with timeout to prevent hangs
            track = await asyncio.wait_for(
                asyncio.to_thread(self.client.get_track, artist, title),
                timeout=settings.api.lastfm_request_timeout,
            )
            await asyncio.wait_for(
                asyncio.to_thread(track.love),
                timeout=settings.api.lastfm_request_timeout,
            )
            return True
        except TimeoutError:
            logger.warning(f"Timeout loving track '{artist} - {title}'")
            return False
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
        if not self.is_configured or self.client is None:
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

            # Use asyncio.to_thread with timeout to prevent hangs
            # Get Last.fm user
            lastfm_user = await asyncio.wait_for(
                asyncio.to_thread(self.client.get_user, user),
                timeout=settings.api.lastfm_request_timeout,
            )
            if not lastfm_user:
                logger.error(f"Could not get Last.fm user: {user}")
                return []

            # Get recent tracks using pylast (time-range based fetching)
            # This can be slow for large requests, so use longer timeout
            recent_tracks = await asyncio.wait_for(
                asyncio.to_thread(
                    lastfm_user.get_recent_tracks,
                    limit=params["limit"],
                    time_from=params.get("time_from"),
                    time_to=params.get("time_to"),
                ),
                timeout=settings.api.lastfm_request_timeout
                * 2,  # Double timeout for batch requests
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
                album_name = played_track.album or None

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

        except TimeoutError:
            logger.warning(f"Timeout fetching recent tracks for user {user}")
            return []
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
            return await asyncio.wait_for(
                asyncio.to_thread(self._extract_metadata_sync, track),
                timeout=settings.api.lastfm_request_timeout,
            )
        except TimeoutError:
            logger.warning("Timeout extracting track metadata")
            return {}
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
