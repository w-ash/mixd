"""Last.fm API client - Pure API wrapper using native httpx with JSON responses.

Provides a thin async wrapper around the Last.fm Web Services API using
httpx.AsyncClient directly. All requests use JSON format (format=json param),
eliminating the XML parsing required by the previous pylast-based implementation.

Key components:
- LastFMAPIClient: API key + session-authenticated client
- Session key auth via auth.getMobileSession for write operations
- Centralized retry policy using tenacity
"""

import asyncio
from datetime import datetime
import hashlib
from typing import Any

from attrs import define, field
import httpx
from pydantic import ValidationError
from tenacity import AsyncRetrying

from src.config import get_logger, settings
from src.config.constants import LastFMConstants
from src.infrastructure.connectors._shared.http_client import (
    log_error_response_body,
    make_lastfm_client,
)
from src.infrastructure.connectors._shared.retry_policies import RetryPolicyFactory
from src.infrastructure.connectors.lastfm.models import (
    LastFMAPIError,
    LastFMRecentTracksPage,
    LastFMTrackEntry,
)

logger = get_logger(__name__).bind(service="lastfm_client")


# -------------------------------------------------------------------------
# SIGNATURE HELPER
# -------------------------------------------------------------------------


def _sign_params(params: dict[str, str], api_secret: str) -> str:
    """Compute Last.fm API signature.

    Algorithm: md5(sorted_key_value_pairs_concatenated + api_secret)
    The "format" parameter is excluded per Last.fm API specification.

    Args:
        params: Request parameters (without "format" and "api_sig")
        api_secret: Last.fm API secret key

    Returns:
        Hex-encoded MD5 signature string
    """
    sorted_pairs = "".join(k + v for k, v in sorted(params.items()))
    return hashlib.md5((sorted_pairs + api_secret).encode()).hexdigest()  # noqa: S324 — Last.fm API signature scheme requires MD5


# -------------------------------------------------------------------------
# CLIENT
# -------------------------------------------------------------------------


@define(slots=True)
class LastFMAPIClient:
    """Last.fm API client using native httpx with JSON format.

    Reads track info, recent tracks, and love tracks via the Last.fm Web Services.
    Authenticated write operations use a session key obtained via auth.getMobileSession.

    Example:
        >>> client = LastFMAPIClient()
        >>> info = await client.get_track_info_comprehensive("Radiohead", "Creep")
    """

    api_key: str | None = field(default=None)
    api_secret: str | None = field(default=None)
    lastfm_username: str | None = field(default=None)
    _session_key: str | None = field(default=None, init=False, repr=False)
    _session_lock: asyncio.Lock = field(factory=asyncio.Lock, init=False, repr=False)
    _retry_policy: AsyncRetrying = field(init=False, repr=False)

    def __attrs_post_init__(self) -> None:
        """Resolve credentials from settings and initialize retry policy."""
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

        from src.infrastructure.connectors.lastfm.error_classifier import (
            LastFMErrorClassifier,
        )

        self._retry_policy = RetryPolicyFactory.create_lastfm_policy(
            classifier=LastFMErrorClassifier(),
            service_error_types=(LastFMAPIError,),
        )

    @property
    def is_configured(self) -> bool:
        """Check if client is properly configured with an API key."""
        return self.api_key is not None

    # -------------------------------------------------------------------------
    # CORE REQUEST BUILDER
    # -------------------------------------------------------------------------

    async def _api_request(
        self,
        method: str,
        params: dict[str, str] | None = None,
        authenticated: bool = False,
    ) -> dict[str, Any]:
        """Make a Last.fm API call and return parsed JSON response.

        Read-only methods use GET with params in the URL query string, matching
        the Last.fm API documentation. Authenticated write operations use POST
        with a form-encoded body and an api_sig covering all parameters.

        Args:
            method: Last.fm API method (e.g. "track.getInfo", "track.love")
            params: Additional parameters for the request
            authenticated: If True, use POST with session key and api_sig

        Returns:
            Parsed JSON response dict

        Raises:
            LastFMAPIError: If the API returns {"error": N, ...} in the body
            httpx.HTTPStatusError: On 4xx/5xx HTTP responses
            httpx.RequestError: On network/connection failures
        """
        base_params: dict[str, str] = {
            "method": method,
            "api_key": self.api_key or "",
            "format": "json",
            **(params or {}),
        }

        async with make_lastfm_client() as client:
            if authenticated:
                sk = await self._get_session_key()
                base_params["sk"] = sk
                # api_sig excludes "format" per Last.fm API spec
                sig_params = {k: v for k, v in base_params.items() if k != "format"}
                base_params["api_sig"] = _sign_params(sig_params, self.api_secret or "")
                try:
                    response = await client.post("/", data=base_params)
                    response.raise_for_status()
                except httpx.HTTPStatusError as e:
                    log_error_response_body(e, method)
                    raise
            else:
                # Read-only methods: GET with params in query string
                # Last.fm API documentation specifies GET for all read operations
                try:
                    response = await client.get("/", params=base_params)
                    response.raise_for_status()
                except httpx.HTTPStatusError as e:
                    log_error_response_body(e, method)
                    raise

            # Last.fm returns errors as HTTP 200 with {"error": N, "message": "..."}
            data = response.json()

        if "error" in data:
            raise LastFMAPIError(data["error"], data.get("message", ""))

        return data

    # -------------------------------------------------------------------------
    # SESSION KEY (WRITE AUTH)
    # -------------------------------------------------------------------------

    async def _get_session_key(self) -> str:
        """Return a valid Last.fm session key, obtaining one if not yet cached.

        Session keys are valid indefinitely. Once obtained, they are cached
        in memory for the lifetime of the client instance.

        Raises:
            RuntimeError: If credentials for auth are not configured.
            LastFMAPIError: If session key acquisition fails.
        """
        async with self._session_lock:
            if self._session_key is not None:
                return self._session_key

            if not (self.api_key and self.api_secret and self.lastfm_username):
                raise RuntimeError(
                    "Last.fm write operations require api_key, api_secret, and username"
                )

            lastfm_password = (
                settings.credentials.lastfm_password.get_secret_value()
                if settings.credentials.lastfm_password
                else None
            )
            if not lastfm_password:
                raise RuntimeError(
                    "Last.fm write operations require a password in credentials"
                )

            # auth.getMobileSession signature: Last.fm mobile auth uses md5(username + md5(password))
            password_hash = hashlib.md5(lastfm_password.encode()).hexdigest()  # noqa: S324 — Last.fm API signature scheme requires MD5
            auth_token = hashlib.md5(  # noqa: S324 — Last.fm API signature scheme requires MD5
                (self.lastfm_username + password_hash).encode()
            ).hexdigest()

            auth_params: dict[str, str] = {
                "method": "auth.getMobileSession",
                "username": self.lastfm_username,
                "authToken": auth_token,
                "api_key": self.api_key,
            }
            api_sig = _sign_params(auth_params, self.api_secret)
            auth_params["api_sig"] = api_sig
            auth_params["format"] = "json"

            async with make_lastfm_client() as client:
                response = await client.post("/", data=auth_params)
                response.raise_for_status()
                data = response.json()

            if "error" in data:
                raise LastFMAPIError(data["error"], data.get("message", ""))

            self._session_key = data["session"]["key"]
            logger.debug("Last.fm session key obtained successfully")
            if self._session_key is None:
                raise RuntimeError("Session key not available after authentication")
            return self._session_key

    # -------------------------------------------------------------------------
    # TRACK INFO API METHODS
    # -------------------------------------------------------------------------

    async def get_track_info_comprehensive(
        self, artist: str, title: str
    ) -> dict[str, Any] | None:
        """Get comprehensive track info in a single API call.

        Returns a dict with lastfm_* prefixed keys matching LastFMTrackInfo fields.
        """
        try:
            return await self._get_track_info_comprehensive_with_retries(artist, title)
        except (LastFMAPIError, httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.warning(f"Failed to get comprehensive track info after retries: {e}")
            return None

    async def _get_track_info_comprehensive_with_retries(
        self, artist: str, title: str
    ) -> dict[str, Any] | None:
        """Get comprehensive track info with retry policy."""
        return await self._retry_policy(
            self._get_track_info_comprehensive_impl, artist, title
        )

    async def _get_track_info_comprehensive_impl(
        self, artist: str, title: str
    ) -> dict[str, Any] | None:
        """Pure implementation without retry logic."""
        if not self.is_configured:
            return None

        params: dict[str, str] = {"artist": artist, "track": title, "autocorrect": "1"}
        if self.lastfm_username:
            params["username"] = self.lastfm_username

        data = await self._api_request("track.getInfo", params)
        return _parse_track_info(data, has_user_data=bool(self.lastfm_username))

    async def get_track_info_comprehensive_by_mbid(
        self, mbid: str
    ) -> dict[str, Any] | None:
        """Get comprehensive track info by MusicBrainz ID."""
        try:
            return await self._get_track_info_comprehensive_by_mbid_with_retries(mbid)
        except (LastFMAPIError, httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.warning(
                f"Failed to get comprehensive track info by MBID after retries: {e}"
            )
            return None

    async def _get_track_info_comprehensive_by_mbid_with_retries(
        self, mbid: str
    ) -> dict[str, Any] | None:
        """Get comprehensive track info by MBID with retry policy."""
        return await self._retry_policy(
            self._get_track_info_comprehensive_by_mbid_impl, mbid
        )

    async def _get_track_info_comprehensive_by_mbid_impl(
        self, mbid: str
    ) -> dict[str, Any] | None:
        """Pure implementation without retry logic."""
        if not self.is_configured or not mbid:
            return None

        params: dict[str, str] = {"mbid": mbid, "autocorrect": "1"}
        if self.lastfm_username:
            params["username"] = self.lastfm_username

        data = await self._api_request("track.getInfo", params)
        return _parse_track_info(data, has_user_data=bool(self.lastfm_username))

    # -------------------------------------------------------------------------
    # TRACK LOVE (WRITE)
    # -------------------------------------------------------------------------

    async def love_track(self, artist: str, title: str) -> bool:
        """Love a track for the authenticated user.

        Single API call — no intermediate track lookup required.
        """
        if not self.is_configured or not self.lastfm_username:
            logger.warning("Cannot love track - no username configured")
            return False

        try:
            return await self._love_track_with_retries(artist, title)
        except (LastFMAPIError, httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.warning(f"Failed to love track after retries: {e}")
            return False

    async def _love_track_with_retries(self, artist: str, title: str) -> bool:
        """Love track with retry policy."""
        return await self._retry_policy(self._love_track_impl, artist, title)

    async def _love_track_impl(self, artist: str, title: str) -> bool:
        """Pure implementation without retry logic."""
        await self._api_request(
            "track.love",
            params={"artist": artist, "track": title},
            authenticated=True,
        )
        return True

    # -------------------------------------------------------------------------
    # RECENT TRACKS
    # -------------------------------------------------------------------------

    async def get_recent_tracks(
        self,
        username: str | None = None,
        limit: int = 200,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
    ) -> list[LastFMTrackEntry]:
        """Get recent tracks from Last.fm user.getRecentTracks API.

        Fetches multiple pages as needed to satisfy the requested total limit.
        Each page is individually retried on failure; a single-page failure
        stops iteration but returns all tracks gathered so far.

        Args:
            username: Last.fm username (defaults to configured username)
            limit: Total number of tracks to return (pagination handled automatically)
            from_time: Beginning timestamp (UTC)
            to_time: End timestamp (UTC)

        Returns:
            Validated LastFMTrackEntry objects, newest-first, up to `limit` entries.
        """
        if not self.is_configured:
            logger.error("Last.fm client not configured")
            return []

        user = username or self.lastfm_username
        if not user:
            logger.error("No Last.fm username provided")
            return []

        all_tracks: list[LastFMTrackEntry] = []
        page = 1
        total_pages = 1

        while page <= total_pages and len(all_tracks) < limit:
            try:
                tracks, total_pages = await self._get_recent_tracks_with_retries(
                    user, page, from_time, to_time
                )
            except (LastFMAPIError, httpx.HTTPStatusError, httpx.RequestError) as e:
                logger.warning(f"Failed to get recent tracks page {page}: {e}")
                break

            all_tracks.extend(tracks)
            if not tracks:  # Empty page → stop early
                break
            page += 1

        logger.info(f"Retrieved {len(all_tracks)} recent tracks ({page - 1} pages)")
        return all_tracks[:limit]

    async def _get_recent_tracks_with_retries(
        self,
        username: str | None = None,
        page: int = 1,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
    ) -> tuple[list[LastFMTrackEntry], int]:
        """Fetch a single page of recent tracks with retry policy."""
        return await self._retry_policy(
            self._get_recent_tracks_impl, username, page, from_time, to_time
        )

    async def _get_recent_tracks_impl(
        self,
        username: str | None = None,
        page: int = 1,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
    ) -> tuple[list[LastFMTrackEntry], int]:
        """Fetch ONE page from user.getRecentTracks. Returns (validated entries, total_pages)."""
        params: dict[str, str] = {
            "user": username or "",
            "limit": str(LastFMConstants.RECENT_TRACKS_PAGE_SIZE),
            "page": str(page),
            "extended": "1",  # loved status (userloved) + artist URL per track; no extra cost
        }
        if from_time:
            params["from"] = str(int(from_time.timestamp()))
        if to_time:
            params["to"] = str(int(to_time.timestamp()))

        data = await self._api_request("user.getRecentTracks", params)

        try:
            page_data = LastFMRecentTracksPage.model_validate(
                data.get("recenttracks", {})
            )
        except ValidationError as e:
            logger.warning(f"Unexpected Last.fm response shape on page {page}: {e}")
            return [], 1

        return page_data.playable_tracks, page_data.total_pages


def _parse_int(value: str | int | None) -> int | None:
    """Parse an integer from a Last.fm API response value."""
    if value is None:
        return None
    try:
        return int(value)
    except ValueError, TypeError:
        return None


def _parse_track_info(
    data: dict[str, Any], has_user_data: bool
) -> dict[str, Any] | None:
    """Extract lastfm_* metadata dict from a track.getInfo JSON response.

    Output keys match LastFMTrackInfo field names (lastfm_* prefix) for
    backward compatibility with LastFMTrackInfo.from_comprehensive_data().

    Args:
        data: Parsed JSON response from track.getInfo
        has_user_data: Whether user-specific fields (playcount, loved) are expected

    Returns:
        Dict with lastfm_* prefixed keys, or None if no track data present
    """
    track_data = data.get("track", {})
    if not track_data:
        return None

    result: dict[str, Any] = {
        "lastfm_title": track_data.get("name"),
        "lastfm_mbid": track_data.get("mbid") or None,
        "lastfm_url": track_data.get("url"),
        "lastfm_duration": _parse_int(track_data.get("duration")),
        "lastfm_global_playcount": _parse_int(track_data.get("playcount")),
        "lastfm_listeners": _parse_int(track_data.get("listeners")),
    }

    artist_data = track_data.get("artist", {})
    if isinstance(artist_data, dict):
        result["lastfm_artist_name"] = artist_data.get("name")
        result["lastfm_artist_mbid"] = artist_data.get("mbid") or None
        result["lastfm_artist_url"] = artist_data.get("url")

    album_data = track_data.get("album", {})
    if isinstance(album_data, dict):
        # Last.fm uses "title" for album name in track.getInfo
        result["lastfm_album_name"] = album_data.get("title")
        result["lastfm_album_mbid"] = album_data.get("mbid") or None
        result["lastfm_album_url"] = album_data.get("url")

    if has_user_data:
        result["lastfm_user_playcount"] = _parse_int(track_data.get("userplaycount"))
        result["lastfm_user_loved"] = track_data.get("userloved") == "1"

    return result
