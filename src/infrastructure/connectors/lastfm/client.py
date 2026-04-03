"""Last.fm API client - Pure API wrapper using native httpx with JSON responses.

Provides a thin async wrapper around the Last.fm Web Services API using
httpx.AsyncClient directly. All requests use JSON format (format=json param),
eliminating the XML parsing required by the previous pylast-based implementation.

Key components:
- LastFMAPIClient: API key + session-authenticated client
- Session key auth via auth.getMobileSession for write operations
- Centralized retry policy using tenacity
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: API response data, framework types

from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
from typing import Any, ClassVar, override
from urllib.parse import quote as _percent_encode

from attrs import define, field
import httpx
from pydantic import ValidationError
from tenacity import AsyncRetrying

from src.config import get_logger, settings
from src.config.constants import LastFMConstants
from src.infrastructure.connectors._shared.retry_policies import (
    RetryConfig,
    RetryPolicyFactory,
)
from src.infrastructure.connectors._shared.token_storage import TokenStorage
from src.infrastructure.connectors.base import BaseAPIClient
from src.infrastructure.connectors.lastfm.conversions import LastFMTrackInfo
from src.infrastructure.connectors.lastfm.models import (
    LastFMAPIError,
    LastFMRecentTracksPage,
    LastFMTrackEntry,
    LastFMTrackInfoData,
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
    return hashlib.md5(
        (sorted_pairs + api_secret).encode(), usedforsecurity=False
    ).hexdigest()


# -------------------------------------------------------------------------
# CLIENT
# -------------------------------------------------------------------------


def _default_user_id() -> str:
    """Read current user_id from ContextVar (deferred to avoid circular import)."""
    from src.infrastructure.persistence.database.user_context import (
        get_current_user_id_from_context,
    )

    return get_current_user_id_from_context()


@define(slots=True)
class LastFMAPIClient(BaseAPIClient):
    """Last.fm API client using native httpx with JSON format.

    Reads track info, recent tracks, and love tracks via the Last.fm Web Services.
    Authenticated write operations use a session key obtained via auth.getMobileSession.

    Example:
        >>> client = LastFMAPIClient()
        >>> info = await client.get_track_info_comprehensive("Radiohead", "Creep")
    """

    _SUPPRESS_ERRORS: ClassVar[tuple[type[BaseException], ...]] = (
        LastFMAPIError,
        httpx.HTTPStatusError,
        httpx.RequestError,
    )

    api_key: str | None = field(default=None)
    api_secret: str | None = field(default=None)
    lastfm_username: str | None = field(default=None)
    user_id: str = field(factory=_default_user_id)
    _session_key: str | None = field(default=None, init=False, repr=False)
    _session_lock: asyncio.Lock = field(factory=asyncio.Lock, init=False, repr=False)
    _retry_policy: AsyncRetrying = field(init=False, repr=False)
    _client: httpx.AsyncClient = field(init=False, repr=False)
    _storage: TokenStorage = field(init=False, repr=False)

    def __attrs_post_init__(self) -> None:
        """Resolve credentials from settings, initialize retry policy, and create pooled client."""
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

        from src.infrastructure.connectors._shared.token_storage import (
            get_token_storage,
        )
        from src.infrastructure.connectors.lastfm.error_classifier import (
            LastFMErrorClassifier,
        )

        self._storage = get_token_storage()
        self._retry_policy = RetryPolicyFactory.create_policy(
            RetryConfig(
                service_name="lastfm",
                classifier=LastFMErrorClassifier(),
                max_attempts=settings.api.lastfm.retry_count,
                wait_multiplier=settings.api.lastfm.retry_base_delay,
                wait_max=settings.api.lastfm.retry_max_delay,
                max_delay=settings.api.lastfm.retry_max_delay,
                service_error_types=(LastFMAPIError,),
            )
        )
        from src.infrastructure.connectors._shared.http_client import make_lastfm_client

        self._client = make_lastfm_client()

    @override
    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()

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

        if authenticated:
            sk = await self.get_session_key()
            base_params["sk"] = sk
            # api_sig excludes "format" per Last.fm API spec
            sig_params = {k: v for k, v in base_params.items() if k != "format"}
            base_params["api_sig"] = _sign_params(sig_params, self.api_secret or "")

        # Work around Last.fm's double URL decoding (see
        # https://support.last.fm/t/api-and-website-are-decoding-url-parameters-twice/116278).
        # Pre-encode values so httpx sends double-encoded params (%252B etc.)
        # that Last.fm correctly double-decodes back to the original characters.
        encoded_params = {
            k: _percent_encode(v, safe="") for k, v in base_params.items()
        }

        if authenticated:
            response = await self._client.post("/", data=encoded_params)
            _ = response.raise_for_status()
        else:
            # Read-only methods: GET with params in query string
            # Last.fm API documentation specifies GET for all read operations
            response = await self._client.get("/", params=encoded_params)
            _ = response.raise_for_status()

        # Last.fm returns errors as HTTP 200 with {"error": N, "message": "..."}
        data = response.json()

        if "error" in data:
            raise LastFMAPIError(data["error"], data.get("message", ""))

        return data

    # -------------------------------------------------------------------------
    # SESSION KEY (WRITE AUTH)
    # -------------------------------------------------------------------------

    async def get_session_key(self) -> str:
        """Return a valid Last.fm session key, obtaining one if not yet cached.

        Resolution order:
        1. In-memory cache (fastest, within same process)
        2. Database/file storage (survives restarts)
        3. auth.getMobileSession (password-based, persists result to storage)

        Session keys are valid indefinitely per Last.fm API.

        Raises:
            RuntimeError: If credentials for auth are not configured.
            LastFMAPIError: If session key acquisition fails.
        """
        async with self._session_lock:
            if self._session_key is not None:
                return self._session_key

            # Check persistent storage (database or file)
            stored = await self._storage.load_token("lastfm", self.user_id)
            stored_key = stored.get("session_key") if stored else None
            if stored_key:
                self._session_key = stored_key
                logger.debug("Last.fm session key loaded from storage")
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
                    "Last.fm write operations require a password in credentials "
                    "or a session key stored via web auth"
                )

            # auth.getMobileSession signature: Last.fm mobile auth uses md5(username + md5(password))
            password_hash = hashlib.md5(
                lastfm_password.encode(), usedforsecurity=False
            ).hexdigest()
            auth_token = hashlib.md5(
                (self.lastfm_username + password_hash).encode(), usedforsecurity=False
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

            response = await self._client.post("/", data=auth_params)
            _ = response.raise_for_status()
            data = response.json()

            if "error" in data:
                raise LastFMAPIError(data["error"], data.get("message", ""))

            self._session_key = data["session"]["key"]
            logger.debug("Last.fm session key obtained via mobile auth")
            if self._session_key is None:
                raise RuntimeError("Session key not available after authentication")

            # Persist to storage so it survives restarts
            await self._storage.save_token(
                "lastfm",
                self.user_id,
                {
                    "session_key": self._session_key,
                    "token_type": "session",
                    "account_name": self.lastfm_username,
                },
            )

            return self._session_key

    async def exchange_web_auth_token(self, token: str) -> tuple[str, str]:
        """Exchange a Last.fm web auth token for a permanent session key.

        This is the ``auth.getSession`` flow — used after browser-based
        authorization where the user approves access on last.fm and is
        redirected back with a temporary token.

        Both the web callback route and the CLI ``connectors auth lastfm``
        command share this method to avoid duplicating the exchange logic.

        Args:
            token: Temporary token from the Last.fm web auth redirect.

        Returns:
            Tuple of (session_key, username).

        Raises:
            LastFMAPIError: If the API returns an error response.
            RuntimeError: If credentials are missing or response is malformed.
        """
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Last.fm web auth requires api_key and api_secret")

        sig_params: dict[str, str] = {
            "method": "auth.getSession",
            "api_key": self.api_key,
            "token": token,
        }
        api_sig = _sign_params(sig_params, self.api_secret)
        request_params = {**sig_params, "api_sig": api_sig, "format": "json"}

        response = await self._client.get("/", params=request_params)
        _ = response.raise_for_status()
        data = response.json()

        if "error" in data:
            raise LastFMAPIError(data["error"], data.get("message", ""))

        raw_session = data.get("session")
        if not isinstance(raw_session, dict):
            raise TypeError("Last.fm auth.getSession returned no session object")

        session_key = str(raw_session.get("key", ""))
        username = str(raw_session.get("name", ""))

        if not session_key:
            raise RuntimeError("Last.fm auth.getSession returned no session key")

        return session_key, username

    # -------------------------------------------------------------------------
    # TRACK INFO API METHODS
    # -------------------------------------------------------------------------

    async def get_track_info_comprehensive(
        self, artist: str, title: str
    ) -> LastFMTrackInfo | None:
        """Get comprehensive track info in a single API call."""
        return await self._api_call(
            "get_lastfm_track_info_comprehensive",
            self._get_track_info_comprehensive_impl,
            artist,
            title,
        )

    async def _get_track_info_comprehensive_impl(
        self, artist: str, title: str
    ) -> LastFMTrackInfo | None:
        """Pure implementation without retry logic."""
        if not self.is_configured:
            return None

        params: dict[str, str] = {"artist": artist, "track": title, "autocorrect": "1"}
        if self.lastfm_username:
            params["username"] = self.lastfm_username

        data = await self._api_request("track.getInfo", params)
        return _validate_track_info(data, has_user_data=bool(self.lastfm_username))

    async def get_track_info_comprehensive_by_mbid(
        self, mbid: str
    ) -> LastFMTrackInfo | None:
        """Get comprehensive track info by MusicBrainz ID."""
        return await self._api_call(
            "get_lastfm_track_info_by_mbid",
            self._get_track_info_comprehensive_by_mbid_impl,
            mbid,
        )

    async def _get_track_info_comprehensive_by_mbid_impl(
        self, mbid: str
    ) -> LastFMTrackInfo | None:
        """Pure implementation without retry logic."""
        if not self.is_configured or not mbid:
            return None

        params: dict[str, str] = {"mbid": mbid, "autocorrect": "1"}
        if self.lastfm_username:
            params["username"] = self.lastfm_username

        data = await self._api_request("track.getInfo", params)
        return _validate_track_info(data, has_user_data=bool(self.lastfm_username))

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

        result = await self._api_call(
            "lastfm_love_track", self._love_track_impl, artist, title
        )
        return result if result is not None else False

    async def _love_track_impl(self, artist: str, title: str) -> bool:
        """Pure implementation without retry logic."""
        _ = await self._api_request(
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
            result = await self._api_call(
                "get_lastfm_recent_tracks",
                self._get_recent_tracks_impl,
                user,
                page,
                from_time,
                to_time,
            )
            if result is None:
                break

            tracks, total_pages = result
            all_tracks.extend(tracks)
            if not tracks:  # Empty page → stop early
                break
            page += 1

        logger.info(f"Retrieved {len(all_tracks)} recent tracks ({page - 1} pages)")
        return all_tracks[:limit]

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


def _validate_track_info(
    data: dict[str, Any], has_user_data: bool
) -> LastFMTrackInfo | None:
    """Validate a track.getInfo JSON response and convert to domain model.

    Uses LastFMTrackInfoData Pydantic model for boundary validation,
    then constructs the attrs domain model directly.

    Args:
        data: Parsed JSON response from track.getInfo
        has_user_data: Whether user-specific fields (playcount, loved) are expected

    Returns:
        LastFMTrackInfo domain model, or None if no track data present
    """
    track_node = data.get("track")
    if not track_node:
        return None

    validated = LastFMTrackInfoData.model_validate(track_node)
    return LastFMTrackInfo.from_track_info_response(
        validated, has_user_data=has_user_data
    )
