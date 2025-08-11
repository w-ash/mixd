"""MusicBrainz API client - Pure API wrapper.

This module provides a thin wrapper around the musicbrainzngs library for MusicBrainz API
interactions. It handles authentication, individual API calls, and rate limiting
to comply with MusicBrainz API policies (1 request per second).

Key components:
- MusicBrainzAPIClient: Authenticated client for individual API calls
- Rate limiting with proper backoff to ensure API policy compliance
- Basic retry and error handling for API requests

The client is stateless and focuses purely on API communication. Complex
workflows and business logic are handled in separate operation modules.
"""

import asyncio
from importlib.metadata import metadata
import time
from typing import Any

from attrs import define, field
import backoff
import musicbrainzngs

from src.config import get_logger, resilient_operation

# Get contextual logger for API client operations
logger = get_logger(__name__).bind(service="musicbrainz_client")

# Configure MusicBrainz client globally
pkg_meta = metadata("narada")
app_name = pkg_meta.get("Name", "Narada")
app_version = pkg_meta.get("Version", "0.1.0") 
app_url = pkg_meta.get("Home-page", "https://github.com/user/narada")
musicbrainzngs.set_useragent(app_name, app_version, app_url)


@define(slots=True)
class MusicBrainzAPIClient:
    """Pure MusicBrainz API client with rate limiting.
    
    Provides thin wrapper around musicbrainzngs with proper rate limiting
    and individual API method calls. No business logic or complex orchestration.
    """

    _last_request_time: float = field(default=0.0, init=False, repr=False)
    _request_lock: asyncio.Lock = field(factory=asyncio.Lock, init=False, repr=False)

    @property
    def connector_name(self) -> str:
        """Service identifier for this connector."""
        return "musicbrainz"

    # Individual Recording API Methods

    @resilient_operation("musicbrainz_get_recording_by_isrc")
    @backoff.on_exception(backoff.expo, Exception, max_tries=3)
    async def get_recording_by_isrc(self, isrc: str) -> str | None:
        """Get recording MBID by ISRC with rate limiting."""
        if not isrc:
            return None

        try:
            result = await self._rate_limited_request(
                musicbrainzngs.search_recordings,
                query=f"isrc:{isrc}",
                limit=1,
                strict=True,
            )

            recordings = result.get("recording-list", [])
            if recordings:
                recording = recordings[0]
                mbid = recording.get("id")
                logger.debug(f"Found MBID {mbid} for ISRC {isrc}")
                return mbid
            else:
                logger.debug(f"No recording found for ISRC {isrc}")
                return None

        except Exception as e:
            logger.error(f"MusicBrainz ISRC lookup failed for {isrc}: {e}")
            raise

    @resilient_operation("musicbrainz_search_recording")
    @backoff.on_exception(backoff.expo, Exception, max_tries=3)
    async def search_recording(self, artist: str, title: str) -> dict | None:
        """Search for recording by artist and title."""
        if not artist or not title:
            return None

        try:
            query = f'recording:"{title}" AND artist:"{artist}"'
            result = await self._rate_limited_request(
                musicbrainzngs.search_recordings,
                query=query,
                limit=1,
                strict=True,
            )

            recordings = result.get("recording-list", [])
            if recordings:
                return recordings[0]
            return None

        except Exception as e:
            logger.error(f"MusicBrainz search failed for '{artist} - {title}': {e}")
            raise

    # Rate Limiting

    async def _rate_limited_request(self, func, *args, **kwargs) -> Any:
        """Execute MusicBrainz API request with rate limiting (1 req/sec)."""
        async with self._request_lock:
            # Enforce 1-second minimum interval between requests
            current_time = time.time()
            time_since_last = current_time - self._last_request_time
            
            if time_since_last < 1.0:
                sleep_time = 1.0 - time_since_last
                logger.debug(f"Rate limiting: sleeping {sleep_time:.2f}s")
                await asyncio.sleep(sleep_time)

            # Execute the request in a thread to avoid blocking
            try:
                result = await asyncio.to_thread(func, *args, **kwargs)
                self._last_request_time = time.time()
                return result
            except Exception:
                self._last_request_time = time.time()  # Update even on error
                raise