"""Spotify API client - Pure API wrapper using native httpx.

Provides a thin async wrapper around the Spotify Web API using httpx.AsyncClient
directly. All methods are natively async — no asyncio.to_thread() bridging.

Key components:
- SpotifyAPIClient: Token-authenticated client for all API calls
- SpotifyTokenManager handles OAuth 2.0 token lifecycle (auth.py)
- Centralized retry policy using tenacity (retry_policies.py)
- Market-aware API calls with configurable timeouts
"""

from typing import Any

from attrs import define, field
import httpx
from tenacity import AsyncRetrying

from src.config import get_logger, resilient_operation, settings
from src.config.constants import SpotifyConstants
from src.infrastructure.connectors._shared.http_client import (
    log_error_response_body,
    make_spotify_client,
)
from src.infrastructure.connectors._shared.retry_policies import RetryPolicyFactory
from src.infrastructure.connectors.spotify.auth import SpotifyTokenManager

logger = get_logger(__name__).bind(service="spotify_client")

_HTTP_UNAUTHORIZED = 401


@define(slots=True)
class SpotifyAPIClient:
    """Pure Spotify API client using native httpx.

    Provides thin wrappers around the Spotify Web API with authentication,
    centralized retry policy, and individual API method calls. No business
    logic or complex orchestration.

    Example:
        >>> client = SpotifyAPIClient()
        >>> track_data = await client.get_track("4iV5W9uYEdYUVa79Axb7Rh")
        >>> playlist_data = await client.get_playlist("37i9dQZF1DX0XUsuxWHRQd")
    """

    _token_manager: SpotifyTokenManager = field(init=False, repr=False)
    _retry_policy: AsyncRetrying = field(init=False, repr=False)

    @property
    def market(self) -> str:
        """Get configured Spotify market for API requests."""
        return settings.api.spotify_market

    def __attrs_post_init__(self) -> None:
        """Initialize token manager and retry policy."""
        logger.debug("Initializing Spotify API client")
        self._token_manager = SpotifyTokenManager()
        from src.infrastructure.connectors.spotify.error_classifier import (
            SpotifyErrorClassifier,
        )

        self._retry_policy = RetryPolicyFactory.create_spotify_policy(
            classifier=SpotifyErrorClassifier(),
        )

    # -------------------------------------------------------------------------
    # Track API Methods
    # -------------------------------------------------------------------------

    async def get_tracks_bulk(self, track_ids: list[str]) -> dict[str, Any] | None:
        """Fetch multiple tracks from Spotify (up to 50 per request)."""
        try:
            return await self._get_tracks_bulk_with_retries(track_ids)
        except httpx.HTTPStatusError, httpx.RequestError:
            return None

    @resilient_operation("get_spotify_tracks_bulk")
    async def _get_tracks_bulk_with_retries(
        self, track_ids: list[str]
    ) -> dict[str, Any] | None:
        """Fetch tracks with retry policy."""
        return await self._retry_policy(self._get_tracks_bulk_impl, track_ids)

    async def _get_tracks_bulk_impl(
        self, track_ids: list[str]
    ) -> dict[str, Any] | None:
        """Pure implementation without retry logic."""
        if not track_ids or len(track_ids) > SpotifyConstants.TRACKS_BULK_LIMIT:
            logger.warning(
                f"Invalid track_ids list: {len(track_ids) if track_ids else 0} items"
            )
            return None

        token = await self._token_manager.get_valid_token()
        async with make_spotify_client(token) as client:
            try:
                response = await client.get(
                    "/tracks",
                    params={"ids": ",".join(track_ids), "market": self.market},
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                log_error_response_body(e, "get_tracks_bulk")
                if e.response.status_code == _HTTP_UNAUTHORIZED:
                    await self._token_manager.force_refresh()
                raise

    # -------------------------------------------------------------------------
    # Search API Methods
    # -------------------------------------------------------------------------

    async def search_by_isrc(self, isrc: str) -> dict[str, Any] | None:
        """Search for a track using ISRC identifier."""
        try:
            return await self._search_by_isrc_with_retries(isrc)
        except httpx.HTTPStatusError, httpx.RequestError:
            return None

    @resilient_operation("search_spotify_by_isrc")
    async def _search_by_isrc_with_retries(self, isrc: str) -> dict[str, Any] | None:
        """Search by ISRC with retry policy."""
        return await self._retry_policy(self._search_by_isrc_impl, isrc)

    async def _search_by_isrc_impl(self, isrc: str) -> dict[str, Any] | None:
        """Pure implementation without retry logic."""
        logger.debug(f"Searching Spotify for ISRC: {isrc}")
        token = await self._token_manager.get_valid_token()
        async with make_spotify_client(token) as client:
            try:
                response = await client.get(
                    "/search",
                    params={
                        "q": f"isrc:{isrc}",
                        "type": "track",
                        "limit": 1,
                        "market": self.market,
                    },
                )
                response.raise_for_status()
                data = response.json()
                tracks = data.get("tracks", {}).get("items", [])
                if not tracks:
                    logger.warning(
                        "Spotify search by ISRC returned no results", isrc=isrc
                    )
                    return None
                return tracks[0]
            except httpx.HTTPStatusError as e:
                log_error_response_body(e, "search_by_isrc")
                if e.response.status_code == _HTTP_UNAUTHORIZED:
                    await self._token_manager.force_refresh()
                raise

    async def search_track(self, artist: str, title: str) -> dict[str, Any] | None:
        """Search for a track by artist and title."""
        try:
            return await self._search_track_with_retries(artist, title)
        except httpx.HTTPStatusError, httpx.RequestError:
            return None

    @resilient_operation("search_spotify_track")
    async def _search_track_with_retries(
        self, artist: str, title: str
    ) -> dict[str, Any] | None:
        """Search track with retry policy."""
        return await self._retry_policy(self._search_track_impl, artist, title)

    async def _search_track_impl(
        self, artist: str, title: str
    ) -> dict[str, Any] | None:
        """Pure implementation without retry logic."""
        query = f"artist:{artist} track:{title}"
        logger.debug(f"Searching Spotify with query: {query}")
        token = await self._token_manager.get_valid_token()
        async with make_spotify_client(token) as client:
            try:
                response = await client.get(
                    "/search",
                    params={
                        "q": query,
                        "type": "track",
                        "limit": 1,
                        "market": self.market,
                    },
                )
                response.raise_for_status()
                data = response.json()
                tracks = data.get("tracks", {}).get("items", [])
                return tracks[0] if tracks else None
            except httpx.HTTPStatusError as e:
                log_error_response_body(e, "search_track")
                if e.response.status_code == _HTTP_UNAUTHORIZED:
                    await self._token_manager.force_refresh()
                raise

    # -------------------------------------------------------------------------
    # Playlist Read Methods
    # -------------------------------------------------------------------------

    async def get_playlist(self, playlist_id: str) -> dict[str, Any] | None:
        """Fetch a Spotify playlist with basic metadata."""
        try:
            return await self._get_playlist_with_retries(playlist_id)
        except httpx.HTTPStatusError, httpx.RequestError:
            return None

    @resilient_operation("get_spotify_playlist")
    async def _get_playlist_with_retries(
        self, playlist_id: str
    ) -> dict[str, Any] | None:
        """Get playlist with retry policy."""
        return await self._retry_policy(self._get_playlist_impl, playlist_id)

    async def _get_playlist_impl(self, playlist_id: str) -> dict[str, Any] | None:
        """Pure implementation without retry logic."""
        token = await self._token_manager.get_valid_token()
        async with make_spotify_client(token) as client:
            try:
                response = await client.get(
                    f"/playlists/{playlist_id}",
                    params={"market": self.market},
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                log_error_response_body(e, "get_playlist")
                if e.response.status_code == _HTTP_UNAUTHORIZED:
                    await self._token_manager.force_refresh()
                raise

    async def get_playlist_tracks(
        self, playlist_id: str, limit: int = 100, offset: int = 0
    ) -> dict[str, Any] | None:
        """Fetch tracks from a Spotify playlist with pagination."""
        try:
            return await self._get_playlist_tracks_with_retries(
                playlist_id, limit, offset
            )
        except httpx.HTTPStatusError, httpx.RequestError:
            return None

    @resilient_operation("get_spotify_playlist_tracks")
    async def _get_playlist_tracks_with_retries(
        self, playlist_id: str, limit: int = 100, offset: int = 0
    ) -> dict[str, Any] | None:
        """Get playlist tracks with retry policy."""
        return await self._retry_policy(
            self._get_playlist_tracks_impl, playlist_id, limit, offset
        )

    async def _get_playlist_tracks_impl(
        self, playlist_id: str, limit: int = 100, offset: int = 0
    ) -> dict[str, Any] | None:
        """Pure implementation without retry logic."""
        token = await self._token_manager.get_valid_token()
        async with make_spotify_client(token) as client:
            try:
                response = await client.get(
                    f"/playlists/{playlist_id}/tracks",
                    params={
                        "limit": min(limit, 100),
                        "offset": offset,
                        "market": self.market,
                    },
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                log_error_response_body(e, "get_playlist_tracks")
                if e.response.status_code == _HTTP_UNAUTHORIZED:
                    await self._token_manager.force_refresh()
                raise

    async def get_next_page(
        self, current_page: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Fetch next page of paginated Spotify API results."""
        if not current_page or not current_page.get("next"):
            return None

        try:
            return await self._get_next_page_with_retries(current_page)
        except httpx.HTTPStatusError, httpx.RequestError:
            return None

    @resilient_operation("get_spotify_next_page")
    async def _get_next_page_with_retries(
        self, current_page: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Get next page with retry policy."""
        return await self._retry_policy(self._get_next_page_impl, current_page)

    async def _get_next_page_impl(
        self, current_page: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Pure implementation without retry logic.

        Spotify's "next" cursor is an absolute URL — must use a client without
        base_url to avoid double-prefixing.
        """
        next_url = current_page.get("next")
        if not next_url:
            return None

        token = await self._token_manager.get_valid_token()
        # Use a plain client with no base_url — next_url is absolute
        async with httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(float(settings.api.spotify_request_timeout or 15)),
        ) as client:
            try:
                response = await client.get(next_url)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                log_error_response_body(e, "get_next_page")
                if e.response.status_code == _HTTP_UNAUTHORIZED:
                    await self._token_manager.force_refresh()
                raise

    # -------------------------------------------------------------------------
    # Playlist Write Methods
    # -------------------------------------------------------------------------

    async def create_playlist(
        self, name: str, description: str = "", public: bool = False
    ) -> dict[str, Any] | None:
        """Create a new empty Spotify playlist for the current user."""
        try:
            return await self._create_playlist_with_retries(name, description, public)
        except httpx.HTTPStatusError, httpx.RequestError:
            return None

    @resilient_operation("create_spotify_playlist")
    async def _create_playlist_with_retries(
        self, name: str, description: str = "", public: bool = False
    ) -> dict[str, Any] | None:
        """Create playlist with retry policy."""
        return await self._retry_policy(
            self._create_playlist_impl, name, description, public
        )

    async def _create_playlist_impl(
        self, name: str, description: str = "", public: bool = False
    ) -> dict[str, Any] | None:
        """Pure implementation without retry logic.

        Uses POST /me/playlists — no user ID prefetch required.
        """
        token = await self._token_manager.get_valid_token()
        async with make_spotify_client(token) as client:
            try:
                response = await client.post(
                    "/me/playlists",
                    json={"name": name, "public": public, "description": description},
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                log_error_response_body(e, "create_playlist")
                if e.response.status_code == _HTTP_UNAUTHORIZED:
                    await self._token_manager.force_refresh()
                raise

    async def playlist_add_items(
        self, playlist_id: str, items: list[str], position: int | None = None
    ) -> dict[str, Any] | None:
        """Add items to a Spotify playlist.

        Args:
            playlist_id: Spotify playlist ID
            items: List of track URIs to add
            position: Optional position to insert at

        Returns:
            API response with new snapshot_id, None if error
        """
        try:
            return await self._playlist_add_items_with_retries(
                playlist_id, items, position
            )
        except httpx.HTTPStatusError, httpx.RequestError:
            return None

    @resilient_operation("add_spotify_playlist_items")
    async def _playlist_add_items_with_retries(
        self, playlist_id: str, items: list[str], position: int | None = None
    ) -> dict[str, Any] | None:
        """Add playlist items with retry policy."""
        return await self._retry_policy(
            self._playlist_add_items_impl, playlist_id, items, position
        )

    async def _playlist_add_items_impl(
        self, playlist_id: str, items: list[str], position: int | None = None
    ) -> dict[str, Any] | None:
        """Pure implementation without retry logic."""
        body: dict[str, Any] = {"uris": items}
        if position is not None:
            body["position"] = position

        token = await self._token_manager.get_valid_token()
        async with make_spotify_client(token) as client:
            try:
                response = await client.post(
                    f"/playlists/{playlist_id}/tracks",
                    json=body,
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                log_error_response_body(e, "playlist_add_items")
                if e.response.status_code == _HTTP_UNAUTHORIZED:
                    await self._token_manager.force_refresh()
                raise

    async def playlist_remove_specific_occurrences_of_items(
        self, playlist_id: str, items: list[dict], snapshot_id: str | None = None
    ) -> dict[str, Any] | None:
        """Remove specific occurrences of items from a Spotify playlist.

        Args:
            playlist_id: Spotify playlist ID
            items: List of items with URIs and optional positions to remove
            snapshot_id: Optional snapshot ID for conflict detection

        Returns:
            API response with new snapshot_id, None if error
        """
        try:
            return (
                await self._playlist_remove_specific_occurrences_of_items_with_retries(
                    playlist_id, items, snapshot_id
                )
            )
        except httpx.HTTPStatusError, httpx.RequestError:
            return None

    @resilient_operation("remove_specific_spotify_playlist_items")
    async def _playlist_remove_specific_occurrences_of_items_with_retries(
        self, playlist_id: str, items: list[dict], snapshot_id: str | None = None
    ) -> dict[str, Any] | None:
        """Remove playlist items with retry policy."""
        return await self._retry_policy(
            self._playlist_remove_specific_occurrences_of_items_impl,
            playlist_id,
            items,
            snapshot_id,
        )

    async def _playlist_remove_specific_occurrences_of_items_impl(
        self, playlist_id: str, items: list[dict], snapshot_id: str | None = None
    ) -> dict[str, Any] | None:
        """Pure implementation without retry logic."""
        body: dict[str, Any] = {"tracks": items}
        if snapshot_id is not None:
            body["snapshot_id"] = snapshot_id

        token = await self._token_manager.get_valid_token()
        async with make_spotify_client(token) as client:
            try:
                response = await client.request(
                    "DELETE",
                    f"/playlists/{playlist_id}/tracks",
                    json=body,
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                log_error_response_body(e, "playlist_remove_items")
                if e.response.status_code == _HTTP_UNAUTHORIZED:
                    await self._token_manager.force_refresh()
                raise

    async def playlist_reorder_items(
        self,
        playlist_id: str,
        range_start: int,
        insert_before: int,
        range_length: int = 1,
        snapshot_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Reorder items in a Spotify playlist.

        Args:
            playlist_id: Spotify playlist ID
            range_start: Start position of items to move
            insert_before: Position to insert items before
            range_length: Number of items to move (default 1)
            snapshot_id: Optional snapshot ID for conflict detection

        Returns:
            API response with new snapshot_id, None if error
        """
        try:
            return await self._playlist_reorder_items_with_retries(
                playlist_id, range_start, insert_before, range_length, snapshot_id
            )
        except httpx.HTTPStatusError, httpx.RequestError:
            return None

    @resilient_operation("reorder_spotify_playlist_items")
    async def _playlist_reorder_items_with_retries(
        self,
        playlist_id: str,
        range_start: int,
        insert_before: int,
        range_length: int = 1,
        snapshot_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Reorder playlist items with retry policy."""
        return await self._retry_policy(
            self._playlist_reorder_items_impl,
            playlist_id,
            range_start,
            insert_before,
            range_length,
            snapshot_id,
        )

    async def _playlist_reorder_items_impl(
        self,
        playlist_id: str,
        range_start: int,
        insert_before: int,
        range_length: int = 1,
        snapshot_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Pure implementation without retry logic."""
        body: dict[str, Any] = {
            "range_start": range_start,
            "insert_before": insert_before,
            "range_length": range_length,
        }
        if snapshot_id is not None:
            body["snapshot_id"] = snapshot_id

        token = await self._token_manager.get_valid_token()
        async with make_spotify_client(token) as client:
            try:
                response = await client.put(
                    f"/playlists/{playlist_id}/tracks",
                    json=body,
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                log_error_response_body(e, "playlist_reorder_items")
                if e.response.status_code == _HTTP_UNAUTHORIZED:
                    await self._token_manager.force_refresh()
                raise

    async def playlist_replace_items(
        self, playlist_id: str, items: list[str]
    ) -> dict[str, Any] | None:
        """Replace all items in a Spotify playlist.

        Args:
            playlist_id: Spotify playlist ID
            items: List of track URIs to set as playlist contents

        Returns:
            API response with new snapshot_id, None if error
        """
        try:
            return await self._playlist_replace_items_with_retries(playlist_id, items)
        except httpx.HTTPStatusError, httpx.RequestError:
            return None

    @resilient_operation("replace_spotify_playlist_items")
    async def _playlist_replace_items_with_retries(
        self, playlist_id: str, items: list[str]
    ) -> dict[str, Any] | None:
        """Replace playlist items with retry policy."""
        return await self._retry_policy(
            self._playlist_replace_items_impl, playlist_id, items
        )

    async def _playlist_replace_items_impl(
        self, playlist_id: str, items: list[str]
    ) -> dict[str, Any] | None:
        """Pure implementation without retry logic."""
        token = await self._token_manager.get_valid_token()
        async with make_spotify_client(token) as client:
            try:
                response = await client.put(
                    f"/playlists/{playlist_id}/tracks",
                    json={"uris": items},
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                log_error_response_body(e, "playlist_replace_items")
                if e.response.status_code == _HTTP_UNAUTHORIZED:
                    await self._token_manager.force_refresh()
                raise

    async def playlist_change_details(
        self, playlist_id: str, name: str | None = None, description: str | None = None
    ) -> None:
        """Update Spotify playlist metadata.

        Args:
            playlist_id: Spotify playlist ID
            name: Optional new playlist name
            description: Optional new playlist description
        """
        await self._playlist_change_details_with_retries(playlist_id, name, description)

    @resilient_operation("update_spotify_playlist_metadata")
    async def _playlist_change_details_with_retries(
        self, playlist_id: str, name: str | None = None, description: str | None = None
    ) -> None:
        """Change playlist details with retry policy."""
        return await self._retry_policy(
            self._playlist_change_details_impl, playlist_id, name, description
        )

    async def _playlist_change_details_impl(
        self, playlist_id: str, name: str | None = None, description: str | None = None
    ) -> None:
        """Pure implementation without retry logic."""
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description

        if not body:
            return

        token = await self._token_manager.get_valid_token()
        async with make_spotify_client(token) as client:
            try:
                response = await client.put(f"/playlists/{playlist_id}", json=body)
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                log_error_response_body(e, "playlist_change_details")
                if e.response.status_code == _HTTP_UNAUTHORIZED:
                    await self._token_manager.force_refresh()
                raise

    # -------------------------------------------------------------------------
    # User Library Methods
    # -------------------------------------------------------------------------

    async def get_saved_tracks(
        self, limit: int = 50, offset: int = 0
    ) -> dict[str, Any] | None:
        """Fetch user's saved/liked tracks from Spotify.

        Args:
            limit: Number of tracks to fetch (max 50)
            offset: Starting position for pagination

        Returns:
            Saved tracks response, None if error
        """
        try:
            return await self._get_saved_tracks_with_retries(limit, offset)
        except httpx.HTTPStatusError, httpx.RequestError:
            return None

    @resilient_operation("get_spotify_saved_tracks")
    async def _get_saved_tracks_with_retries(
        self, limit: int = 50, offset: int = 0
    ) -> dict[str, Any] | None:
        """Get saved tracks with retry policy."""
        return await self._retry_policy(self._get_saved_tracks_impl, limit, offset)

    async def _get_saved_tracks_impl(
        self, limit: int = 50, offset: int = 0
    ) -> dict[str, Any] | None:
        """Pure implementation without retry logic."""
        token = await self._token_manager.get_valid_token()
        async with make_spotify_client(token) as client:
            try:
                response = await client.get(
                    "/me/tracks",
                    params={
                        "limit": min(limit, 50),
                        "offset": offset,
                        "market": self.market,
                    },
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                log_error_response_body(e, "get_saved_tracks")
                if e.response.status_code == _HTTP_UNAUTHORIZED:
                    await self._token_manager.force_refresh()
                raise

    async def get_current_user(self) -> dict[str, Any] | None:
        """Get current Spotify user information.

        Returns:
            User data if authenticated, None otherwise
        """
        try:
            return await self._get_current_user_with_retries()
        except httpx.HTTPStatusError, httpx.RequestError:
            return None

    @resilient_operation("get_spotify_current_user")
    async def _get_current_user_with_retries(self) -> dict[str, Any] | None:
        """Get current user with retry policy."""
        return await self._retry_policy(self._get_current_user_impl)

    async def _get_current_user_impl(self) -> dict[str, Any] | None:
        """Pure implementation without retry logic."""
        token = await self._token_manager.get_valid_token()
        async with make_spotify_client(token) as client:
            try:
                response = await client.get("/me")
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                log_error_response_body(e, "get_current_user")
                if e.response.status_code == _HTTP_UNAUTHORIZED:
                    await self._token_manager.force_refresh()
                raise
