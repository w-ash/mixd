"""Spotify API client - Pure API wrapper using native httpx.

Provides a thin async wrapper around the Spotify Web API using httpx.AsyncClient
directly. All methods are natively async — no asyncio.to_thread() bridging.

Key components:
- SpotifyAPIClient: Token-authenticated client for all API calls
- SpotifyTokenManager handles OAuth 2.0 token lifecycle (auth.py)
- Centralized retry policy using tenacity (retry_policies.py)
- Market-aware API calls with configurable timeouts
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: response.json() wire format, API response dicts

import asyncio
from typing import Any, ClassVar, override

from attrs import define, field
import httpx
from tenacity import AsyncRetrying

from src.config import get_logger, settings
from src.config.constants import SpotifyConstants
from src.config.logging import logging_context
from src.infrastructure.connectors._shared.retry_policies import (
    RetryConfig,
    RetryPolicyFactory,
)
from src.infrastructure.connectors.base import BaseAPIClient
from src.infrastructure.connectors.spotify.auth import SpotifyTokenManager
from src.infrastructure.connectors.spotify.models import (
    SpotifyPaginatedPlaylistItems,
    SpotifyPlaylist,
    SpotifySnapshotResponse,
    SpotifyTrack,
)

logger = get_logger(__name__).bind(service="spotify_client")


@define(slots=True)
class SpotifyAPIClient(BaseAPIClient):
    """Pure Spotify API client using native httpx.

    Provides thin wrappers around the Spotify Web API with authentication,
    centralized retry policy, and individual API method calls. No business
    logic or complex orchestration.

    Example:
        >>> client = SpotifyAPIClient()
        >>> track_data = await client.get_track("4iV5W9uYEdYUVa79Axb7Rh")
        >>> playlist_data = await client.get_playlist("37i9dQZF1DX0XUsuxWHRQd")
    """

    _SUPPRESS_ERRORS: ClassVar[tuple[type[BaseException], ...]] = (
        httpx.HTTPStatusError,
        httpx.RequestError,
    )

    _token_manager: SpotifyTokenManager = field(init=False, repr=False)
    _retry_policy: AsyncRetrying = field(init=False, repr=False)
    _client: httpx.AsyncClient = field(init=False, repr=False)
    _cached_user_id: str | None = field(init=False, default=None, repr=False)

    @property
    def market(self) -> str:
        """Get configured Spotify market for API requests."""
        return settings.api.spotify_market

    def __attrs_post_init__(self) -> None:
        """Initialize token manager, retry policy, and long-lived pooled client."""
        logger.debug("Initializing Spotify API client")
        from src.infrastructure.connectors._shared.token_storage import (
            get_token_storage,
        )

        self._token_manager = SpotifyTokenManager(storage=get_token_storage())
        from src.infrastructure.connectors._shared.http_client import (
            make_spotify_client,
        )
        from src.infrastructure.connectors.spotify.auth import SpotifyBearerAuth
        from src.infrastructure.connectors.spotify.error_classifier import (
            SpotifyErrorClassifier,
        )

        self._retry_policy = RetryPolicyFactory.create_policy(
            RetryConfig(
                service_name="spotify",
                classifier=SpotifyErrorClassifier(),
                max_attempts=settings.api.spotify.retry_count,
                wait_multiplier=settings.api.spotify.retry_base_delay,
                wait_max=settings.api.spotify.retry_max_delay,
            )
        )
        self._client = make_spotify_client(SpotifyBearerAuth(self._token_manager))

    @override
    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()

    # -------------------------------------------------------------------------
    # Track API Methods
    # -------------------------------------------------------------------------

    async def get_track(self, track_id: str) -> SpotifyTrack | None:
        """Fetch a single track from Spotify by ID."""
        data = await self._api_call("get_spotify_track", self._get_track_impl, track_id)
        return SpotifyTrack.model_validate(data) if data else None

    async def _get_track_impl(self, track_id: str) -> dict[str, Any] | None:
        """Pure implementation without retry logic."""
        response = await self._client.get(
            f"/tracks/{track_id}",
            params={"market": self.market},
        )
        _ = response.raise_for_status()
        return response.json()

    async def get_tracks_concurrent(
        self, track_ids: list[str]
    ) -> dict[str, SpotifyTrack]:
        """Fetch multiple tracks concurrently with structured concurrency.

        Uses asyncio.TaskGroup + Semaphore to limit concurrent requests.
        Tracks that fail to fetch are silently skipped (logged as warnings).

        Returns a dict keyed by REQUESTED ID, not the returned track's .id field.
        This distinction matters when Spotify redirects old IDs to new ones —
        the caller needs to find the result using the ID they asked about.
        """
        if not track_ids:
            return {}

        semaphore = asyncio.Semaphore(settings.api.spotify.concurrency)
        results: dict[str, SpotifyTrack] = {}

        async def _fetch_one(tid: str) -> None:
            async with semaphore:
                try:
                    track = await self.get_track(tid)
                    if track is not None:
                        results[tid] = track
                    else:
                        logger.warning(f"Failed to fetch track {tid}")
                except Exception as e:
                    logger.warning(f"Failed to fetch track {tid}: {e}")

        async with asyncio.TaskGroup() as tg:
            for tid in track_ids:
                _ = tg.create_task(_fetch_one(tid))

        return results

    # -------------------------------------------------------------------------
    # Search API Methods
    # -------------------------------------------------------------------------

    async def search_by_isrc(self, isrc: str) -> SpotifyTrack | None:
        """Search for a track using ISRC identifier."""
        data = await self._api_call(
            "search_spotify_by_isrc", self._search_by_isrc_impl, isrc
        )
        return SpotifyTrack.model_validate(data) if data else None

    async def _search_by_isrc_impl(self, isrc: str) -> dict[str, Any] | None:
        """Pure implementation without retry logic."""
        logger.debug(f"Searching Spotify for ISRC: {isrc}")
        response = await self._client.get(
            "/search",
            params={
                "q": f"isrc:{isrc}",
                "type": "track",
                "limit": 1,
                "market": self.market,
            },
        )
        _ = response.raise_for_status()
        data = response.json()
        tracks = data.get("tracks", {}).get("items", [])
        if not tracks:
            logger.warning("Spotify search by ISRC returned no results", isrc=isrc)
            return None
        return tracks[0]

    async def search_track(
        self, artist: str, title: str, limit: int = 5
    ) -> list[SpotifyTrack]:
        """Search for tracks by artist and title.

        Returns multiple candidates so callers can rank by similarity.
        """
        result = await self._api_call(
            "search_spotify_track", self._search_track_impl, artist, title, limit
        )
        if not result:
            return []
        return [SpotifyTrack.model_validate(t) for t in result]

    async def _search_track_impl(
        self, artist: str, title: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Pure implementation without retry logic."""
        query = f"artist:{artist} track:{title}"
        logger.debug(f"Searching Spotify with query: {query}")
        response = await self._client.get(
            "/search",
            params={
                "q": query,
                "type": "track",
                "limit": min(limit, SpotifyConstants.SEARCH_MAX_LIMIT),
                "market": self.market,
            },
        )
        _ = response.raise_for_status()
        data = response.json()
        return data.get("tracks", {}).get("items", [])

    # -------------------------------------------------------------------------
    # Playlist Read Methods
    # -------------------------------------------------------------------------

    async def get_playlist(self, playlist_id: str) -> SpotifyPlaylist | None:
        """Fetch a Spotify playlist with basic metadata."""
        data = await self._api_call(
            "get_spotify_playlist", self._get_playlist_impl, playlist_id
        )
        return SpotifyPlaylist.model_validate(data) if data else None

    async def _get_playlist_impl(self, playlist_id: str) -> dict[str, Any] | None:
        """Pure implementation without retry logic."""
        response = await self._client.get(
            f"/playlists/{playlist_id}",
            params={"market": self.market},
        )
        _ = response.raise_for_status()
        return response.json()

    async def get_playlist_items(
        self, playlist_id: str, limit: int = 100, offset: int = 0
    ) -> SpotifyPaginatedPlaylistItems | None:
        """Fetch items from a Spotify playlist with pagination."""
        data = await self._api_call(
            "get_spotify_playlist_items",
            self._get_playlist_items_impl,
            playlist_id,
            limit,
            offset,
        )
        return SpotifyPaginatedPlaylistItems.model_validate(data) if data else None

    async def _get_playlist_items_impl(
        self, playlist_id: str, limit: int = 100, offset: int = 0
    ) -> dict[str, Any] | None:
        """Pure implementation without retry logic."""
        response = await self._client.get(
            f"/playlists/{playlist_id}/items",
            params={
                "limit": min(limit, 100),
                "offset": offset,
                "market": self.market,
            },
        )
        _ = response.raise_for_status()
        return response.json()

    async def get_next_page(
        self, current_page: SpotifyPaginatedPlaylistItems
    ) -> SpotifyPaginatedPlaylistItems | None:
        """Fetch next page of paginated Spotify API results."""
        if not current_page.next:
            return None

        data = await self._api_call(
            "get_spotify_next_page", self._get_next_page_impl, current_page.next
        )
        return SpotifyPaginatedPlaylistItems.model_validate(data) if data else None

    async def _get_next_page_impl(self, next_url: str) -> dict[str, Any] | None:
        """Pure implementation without retry logic.

        Spotify's "next" cursor is an absolute URL. httpx uses absolute URLs
        as-is when a base_url is set, so self._client handles them correctly.
        """
        response = await self._client.get(next_url)
        _ = response.raise_for_status()
        return response.json()

    # -------------------------------------------------------------------------
    # Playlist Write Methods
    # -------------------------------------------------------------------------

    async def create_playlist(
        self, name: str, description: str = "", public: bool = False
    ) -> SpotifyPlaylist | None:
        """Create a new empty Spotify playlist for the current user."""
        data = await self._api_call(
            "create_spotify_playlist",
            self._create_playlist_impl,
            name,
            description,
            public,
        )
        return SpotifyPlaylist.model_validate(data) if data else None

    async def _create_playlist_impl(
        self, name: str, description: str = "", public: bool = False
    ) -> dict[str, Any] | None:
        """Pure implementation without retry logic.

        Uses POST /me/playlists — no user ID prefetch required.
        """
        response = await self._client.post(
            "/me/playlists",
            json={"name": name, "public": public, "description": description},
        )
        _ = response.raise_for_status()
        return response.json()

    async def playlist_add_items(
        self, playlist_id: str, items: list[str], position: int | None = None
    ) -> SpotifySnapshotResponse | None:
        """Add items to a Spotify playlist.

        Args:
            playlist_id: Spotify playlist ID
            items: List of track URIs to add
            position: Optional position to insert at

        Returns:
            Validated snapshot response, None if error
        """
        data = await self._api_call(
            "add_spotify_playlist_items",
            self._playlist_add_items_impl,
            playlist_id,
            items,
            position,
        )
        return SpotifySnapshotResponse.model_validate(data) if data else None

    async def _playlist_add_items_impl(
        self, playlist_id: str, items: list[str], position: int | None = None
    ) -> dict[str, Any] | None:
        """Pure implementation without retry logic."""
        body: dict[str, Any] = {"uris": items}
        if position is not None:
            body["position"] = position

        response = await self._client.post(
            f"/playlists/{playlist_id}/items",
            json=body,
        )
        _ = response.raise_for_status()
        return response.json()

    async def playlist_remove_specific_occurrences_of_items(
        self,
        playlist_id: str,
        items: list[dict[str, Any]],
        snapshot_id: str | None = None,
    ) -> SpotifySnapshotResponse | None:
        """Remove specific occurrences of items from a Spotify playlist.

        Args:
            playlist_id: Spotify playlist ID
            items: List of items with URIs and optional positions to remove
            snapshot_id: Optional snapshot ID for conflict detection

        Returns:
            Validated snapshot response, None if error
        """
        data = await self._api_call(
            "remove_specific_spotify_playlist_items",
            self._playlist_remove_specific_occurrences_of_items_impl,
            playlist_id,
            items,
            snapshot_id,
        )
        return SpotifySnapshotResponse.model_validate(data) if data else None

    async def _playlist_remove_specific_occurrences_of_items_impl(
        self,
        playlist_id: str,
        items: list[dict[str, Any]],
        snapshot_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Pure implementation without retry logic."""
        body: dict[str, Any] = {"items": items}
        if snapshot_id is not None:
            body["snapshot_id"] = snapshot_id

        response = await self._client.request(
            "DELETE",
            f"/playlists/{playlist_id}/items",
            json=body,
        )
        _ = response.raise_for_status()
        return response.json()

    async def playlist_reorder_items(
        self,
        playlist_id: str,
        range_start: int,
        insert_before: int,
        range_length: int = 1,
        snapshot_id: str | None = None,
    ) -> SpotifySnapshotResponse | None:
        """Reorder items in a Spotify playlist.

        Args:
            playlist_id: Spotify playlist ID
            range_start: Start position of items to move
            insert_before: Position to insert items before
            range_length: Number of items to move (default 1)
            snapshot_id: Optional snapshot ID for conflict detection

        Returns:
            Validated snapshot response, None if error
        """
        data = await self._api_call(
            "reorder_spotify_playlist_items",
            self._playlist_reorder_items_impl,
            playlist_id,
            range_start,
            insert_before,
            range_length,
            snapshot_id,
        )
        return SpotifySnapshotResponse.model_validate(data) if data else None

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

        response = await self._client.put(
            f"/playlists/{playlist_id}/items",
            json=body,
        )
        _ = response.raise_for_status()
        return response.json()

    async def playlist_replace_items(
        self, playlist_id: str, items: list[str]
    ) -> SpotifySnapshotResponse | None:
        """Replace all items in a Spotify playlist.

        Args:
            playlist_id: Spotify playlist ID
            items: List of track URIs to set as playlist contents

        Returns:
            Validated snapshot response, None if error
        """
        data = await self._api_call(
            "replace_spotify_playlist_items",
            self._playlist_replace_items_impl,
            playlist_id,
            items,
        )
        return SpotifySnapshotResponse.model_validate(data) if data else None

    async def _playlist_replace_items_impl(
        self, playlist_id: str, items: list[str]
    ) -> dict[str, Any] | None:
        """Pure implementation without retry logic."""
        response = await self._client.put(
            f"/playlists/{playlist_id}/items",
            json={"uris": items},
        )
        _ = response.raise_for_status()
        return response.json()

    async def playlist_change_details(
        self, playlist_id: str, name: str | None = None, description: str | None = None
    ) -> None:
        """Update Spotify playlist metadata.

        Args:
            playlist_id: Spotify playlist ID
            name: Optional new playlist name
            description: Optional new playlist description
        """
        with logging_context(operation="update_spotify_playlist_metadata"):
            await self._retry_policy(
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

        response = await self._client.put(f"/playlists/{playlist_id}", json=body)
        _ = response.raise_for_status()

    # -------------------------------------------------------------------------
    # User Library Methods
    # -------------------------------------------------------------------------

    async def check_library_contains(self, uris: list[str]) -> dict[str, bool]:
        """Check which items are in the user's saved library.

        Calls Spotify's /me/library/contains endpoint in batches of 40.
        Accepts Spotify URIs (e.g., "spotify:track:{id}").
        Returns a dict mapping each URI to True/False.
        """
        if not uris:
            return {}

        results: dict[str, bool] = {}
        for i in range(0, len(uris), SpotifyConstants.LIBRARY_CONTAINS_BATCH_SIZE):
            batch = uris[i : i + SpotifyConstants.LIBRARY_CONTAINS_BATCH_SIZE]
            data = await self._api_call(
                "check_spotify_library_contains",
                self._check_library_contains_impl,
                batch,
            )
            if data is not None:
                results.update(zip(batch, data, strict=True))
            else:
                results.update(dict.fromkeys(batch, False))
        return results

    async def _check_library_contains_impl(self, uris: list[str]) -> list[bool]:
        """Pure implementation — GET /me/library/contains."""
        response = await self._client.get(
            "/me/library/contains",
            params={"uris": ",".join(uris)},
        )
        _ = response.raise_for_status()
        return response.json()

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
        return await self._api_call(
            "get_spotify_saved_tracks", self._get_saved_tracks_impl, limit, offset
        )

    async def _get_saved_tracks_impl(
        self, limit: int = 50, offset: int = 0
    ) -> dict[str, Any] | None:
        """Pure implementation without retry logic."""
        response = await self._client.get(
            "/me/tracks",
            params={
                "limit": min(limit, 50),
                "offset": offset,
                "market": self.market,
            },
        )
        _ = response.raise_for_status()
        return response.json()

    async def get_current_user(self) -> dict[str, Any] | None:
        """Get current Spotify user information.

        Returns:
            User data if authenticated, None otherwise
        """
        return await self._api_call(
            "get_spotify_current_user", self._get_current_user_impl
        )

    async def _get_current_user_impl(self) -> dict[str, Any] | None:
        """Pure implementation without retry logic."""
        response = await self._client.get("/me")
        _ = response.raise_for_status()
        return response.json()

    async def get_current_user_id(self) -> str | None:
        """Get (and cache) the current user's Spotify ID."""
        if self._cached_user_id is not None:
            return self._cached_user_id
        user_data = await self.get_current_user()
        if user_data and "id" in user_data:
            self._cached_user_id = user_data["id"]
        return self._cached_user_id
