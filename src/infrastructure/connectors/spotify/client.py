"""Spotify API client - Pure API wrapper.

This module provides a thin wrapper around the spotipy library for Spotify API
interactions. It handles authentication, individual API calls, and basic error
handling without any business logic or complex orchestration.

Key components:
- SpotifyAPIClient: OAuth-authenticated client for individual API calls
- Authentication management using SpotifyOAuth
- Basic retry and error handling for API requests
- Market-aware API calls with configurable timeouts

The client is stateless and focuses purely on API communication. Complex
workflows and business logic are handled in separate operation modules.
"""

import asyncio
import os
from typing import Any

from attrs import define, field
import backoff
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth

from src.config import get_logger, resilient_operation, settings
from src.config.constants import SpotifyConstants
from src.infrastructure.connectors._shared.error_classification import (
    create_backoff_handler,
    create_giveup_handler,
    should_giveup_on_error,
)
from src.infrastructure.connectors.spotify.error_classifier import (
    SpotifyErrorClassifier,
)

# Load environment variables for Spotify credentials
load_dotenv()

# Set spotipy environment variables from configuration
os.environ["SPOTIPY_CLIENT_ID"] = os.getenv("SPOTIFY_CLIENT_ID", "")
os.environ["SPOTIPY_CLIENT_SECRET"] = os.getenv("SPOTIFY_CLIENT_SECRET", "")
os.environ["SPOTIPY_REDIRECT_URI"] = os.getenv("SPOTIFY_REDIRECT_URI", "")

# Get contextual logger for API client operations
logger = get_logger(__name__).bind(service="spotify_client")


async def spotify_api_call(client, method_name: str, *args, **kwargs):
    """Execute Spotify API call with consistent error handling.

    Args:
        client: Spotify client instance
        method_name: Name of the method to call
        *args, **kwargs: Arguments to pass to the method

    Returns:
        API response

    Raises:
        spotipy.SpotifyException: For API-related errors
    """
    method = getattr(client, method_name)
    return await asyncio.to_thread(method, *args, **kwargs)


@define(slots=True)
class SpotifyAPIClient:
    """Pure Spotify API client with OAuth authentication.

    Provides thin wrapper around spotipy with authentication, basic retries,
    and individual API method calls. No business logic or complex orchestration.

    Example:
        >>> client = SpotifyAPIClient()
        >>> track_data = await client.get_track("4iV5W9uYEdYUVa79Axb7Rh")
        >>> playlist_data = await client.get_playlist("37i9dQZF1DX0XUsuxWHRQd")
    """

    client: spotipy.Spotify = field(init=False, repr=False)

    @property
    def market(self) -> str:
        """Get configured Spotify market for API requests."""
        return settings.api.spotify_market

    def __attrs_post_init__(self) -> None:
        """Initialize Spotify client with OAuth configuration and timeout settings."""
        logger.debug("Initializing Spotify API client")

        self.client = spotipy.Spotify(
            auth_manager=SpotifyOAuth(
                scope=[
                    "playlist-modify-public",
                    "playlist-modify-private",
                    "playlist-read-private",
                    "playlist-read-collaborative",
                    "user-library-read",
                ],
                open_browser=True,
                cache_handler=spotipy.CacheFileHandler(cache_path=".spotify_cache"),
            ),
            requests_timeout=int(settings.api.spotify_request_timeout or 15),
            retries=int(settings.api.spotify_retries or 5),
        )

    # Individual Track API Methods

    async def get_tracks_bulk(self, track_ids: list[str]) -> dict[str, Any] | None:
        """Fetch multiple tracks from Spotify (up to 50 per request)."""
        try:
            return await self._get_tracks_bulk_with_retries(track_ids)
        except spotipy.SpotifyException:
            # Backoff decorator exhausted retries - return None gracefully
            return None

    @resilient_operation("get_spotify_tracks_bulk")
    @backoff.on_exception(
        backoff.expo,
        spotipy.SpotifyException,
        max_tries=3,
        giveup=should_giveup_on_error(SpotifyErrorClassifier()),
        on_backoff=create_backoff_handler(SpotifyErrorClassifier(), "spotify"),
        on_giveup=create_giveup_handler(SpotifyErrorClassifier(), "spotify"),
    )
    async def _get_tracks_bulk_with_retries(
        self, track_ids: list[str]
    ) -> dict[str, Any] | None:
        """Internal implementation that allows SpotifyException to propagate to backoff decorator."""
        if not track_ids or len(track_ids) > SpotifyConstants.TRACKS_BULK_LIMIT:
            logger.warning(
                f"Invalid track_ids list: {len(track_ids) if track_ids else 0} items"
            )
            return None

        try:
            response = await spotify_api_call(
                self.client, "tracks", track_ids, market=self.market
            )
            return response

        # NOTE: spotipy.SpotifyException exceptions are intentionally NOT caught here
        # They must propagate to the backoff decorator for proper retry logic

        except (ValueError, TypeError, AttributeError) as e:
            # Only catch non-retryable programming/parsing errors, not API errors
            logger.error(f"Failed to fetch tracks bulk: {e}")
            return None

    # Search API Methods

    async def search_by_isrc(self, isrc: str) -> dict[str, Any] | None:
        """Search for a track using ISRC identifier."""
        try:
            return await self._search_by_isrc_with_retries(isrc)
        except spotipy.SpotifyException:
            # Backoff decorator exhausted retries - return None gracefully
            return None

    @resilient_operation("search_spotify_by_isrc")
    @backoff.on_exception(
        backoff.expo,
        spotipy.SpotifyException,
        max_tries=3,
        giveup=should_giveup_on_error(SpotifyErrorClassifier()),
        on_backoff=create_backoff_handler(SpotifyErrorClassifier(), "spotify"),
        on_giveup=create_giveup_handler(SpotifyErrorClassifier(), "spotify"),
    )
    async def _search_by_isrc_with_retries(self, isrc: str) -> dict[str, Any] | None:
        """Internal implementation that allows SpotifyException to propagate to backoff decorator."""
        logger.debug(f"Searching Spotify for ISRC: {isrc}")

        try:
            results = await spotify_api_call(
                self.client,
                "search",
                f"isrc:{isrc}",
                type="track",
                limit=1,
                market=self.market,
            )

            tracks = results.get("tracks", {}).get("items", []) if results else []
            if not tracks:
                logger.warning("Spotify search by ISRC returned no results", isrc=isrc)
                return None

            return tracks[0]

        # NOTE: spotipy.SpotifyException exceptions are intentionally NOT caught here
        # They must propagate to the backoff decorator for proper retry logic

        except (ValueError, TypeError, AttributeError) as e:
            # Only catch non-retryable programming/parsing errors, not API errors
            logger.error(f"ISRC search failed for {isrc}: {e}")
            return None

    async def search_track(self, artist: str, title: str) -> dict[str, Any] | None:
        """Search for a track by artist and title."""
        try:
            return await self._search_track_with_retries(artist, title)
        except spotipy.SpotifyException:
            # Backoff decorator exhausted retries - return None gracefully
            return None

    @resilient_operation("search_spotify_track")
    @backoff.on_exception(
        backoff.expo,
        spotipy.SpotifyException,
        max_tries=3,
        giveup=should_giveup_on_error(SpotifyErrorClassifier()),
        on_backoff=create_backoff_handler(SpotifyErrorClassifier(), "spotify"),
        on_giveup=create_giveup_handler(SpotifyErrorClassifier(), "spotify"),
    )
    async def _search_track_with_retries(
        self, artist: str, title: str
    ) -> dict[str, Any] | None:
        """Internal implementation that allows SpotifyException to propagate to backoff decorator."""
        query = f"artist:{artist} track:{title}"
        logger.debug(f"Searching Spotify with query: {query}")

        try:
            results = await spotify_api_call(
                self.client,
                "search",
                query,
                type="track",
                limit=1,
                market=self.market,
            )

            tracks = results.get("tracks", {}).get("items", []) if results else []
            return tracks[0] if tracks else None

        # NOTE: spotipy.SpotifyException exceptions are intentionally NOT caught here
        # They must propagate to the backoff decorator for proper retry logic

        except (ValueError, TypeError, AttributeError) as e:
            # Only catch non-retryable programming/parsing errors, not API errors
            logger.error(f"Track search failed for '{artist} - {title}': {e}")
            return None

    # Playlist API Methods

    async def get_playlist(self, playlist_id: str) -> dict[str, Any] | None:
        """Fetch a Spotify playlist with basic metadata."""
        try:
            return await self._get_playlist_with_retries(playlist_id)
        except spotipy.SpotifyException:
            # Backoff decorator exhausted retries - return None gracefully
            return None

    @resilient_operation("get_spotify_playlist")
    @backoff.on_exception(
        backoff.expo,
        spotipy.SpotifyException,
        max_tries=3,
        giveup=should_giveup_on_error(SpotifyErrorClassifier()),
        on_backoff=create_backoff_handler(SpotifyErrorClassifier(), "spotify"),
        on_giveup=create_giveup_handler(SpotifyErrorClassifier(), "spotify"),
    )
    async def _get_playlist_with_retries(
        self, playlist_id: str
    ) -> dict[str, Any] | None:
        """Internal implementation that allows SpotifyException to propagate to backoff decorator."""
        try:
            return await spotify_api_call(
                self.client, "playlist", playlist_id, market=self.market
            )

        # NOTE: spotipy.SpotifyException exceptions are intentionally NOT caught here
        # They must propagate to the backoff decorator for proper retry logic

        except (ValueError, TypeError, AttributeError) as e:
            # Only catch non-retryable programming/parsing errors, not API errors
            logger.error(f"Failed to fetch playlist {playlist_id}: {e}")
            return None

    async def get_playlist_tracks(
        self, playlist_id: str, limit: int = 100, offset: int = 0
    ) -> dict[str, Any] | None:
        """Fetch tracks from a Spotify playlist with pagination."""
        try:
            return await self._get_playlist_tracks_with_retries(
                playlist_id, limit, offset
            )
        except spotipy.SpotifyException:
            # Backoff decorator exhausted retries - return None gracefully
            return None

    @resilient_operation("get_spotify_playlist_tracks")
    @backoff.on_exception(
        backoff.expo,
        spotipy.SpotifyException,
        max_tries=3,
        giveup=should_giveup_on_error(SpotifyErrorClassifier()),
        on_backoff=create_backoff_handler(SpotifyErrorClassifier(), "spotify"),
        on_giveup=create_giveup_handler(SpotifyErrorClassifier(), "spotify"),
    )
    async def _get_playlist_tracks_with_retries(
        self, playlist_id: str, limit: int = 100, offset: int = 0
    ) -> dict[str, Any] | None:
        """Internal implementation that allows SpotifyException to propagate to backoff decorator."""
        try:
            return await spotify_api_call(
                self.client,
                "playlist_tracks",
                playlist_id,
                limit=min(limit, 100),
                offset=offset,
                market=self.market,
            )

        # NOTE: spotipy.SpotifyException exceptions are intentionally NOT caught here
        # They must propagate to the backoff decorator for proper retry logic

        except (ValueError, TypeError, AttributeError) as e:
            # Only catch non-retryable programming/parsing errors, not API errors
            logger.error(f"Failed to fetch playlist tracks {playlist_id}: {e}")
            return None

    async def get_next_page(
        self, current_page: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Fetch next page of paginated Spotify API results."""
        if not current_page or not current_page.get("next"):
            return None

        try:
            return await self._get_next_page_with_retries(current_page)
        except spotipy.SpotifyException:
            # Backoff decorator exhausted retries - return None gracefully
            return None

    @resilient_operation("get_spotify_next_page")
    @backoff.on_exception(
        backoff.expo,
        spotipy.SpotifyException,
        max_tries=3,
        giveup=should_giveup_on_error(SpotifyErrorClassifier()),
        on_backoff=create_backoff_handler(SpotifyErrorClassifier(), "spotify"),
        on_giveup=create_giveup_handler(SpotifyErrorClassifier(), "spotify"),
    )
    async def _get_next_page_with_retries(
        self, current_page: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Internal implementation that allows SpotifyException to propagate to backoff decorator."""
        return await spotify_api_call(self.client, "next", current_page)

    async def create_playlist(
        self, name: str, description: str = "", public: bool = False
    ) -> dict[str, Any] | None:
        """Create a new empty Spotify playlist."""
        try:
            return await self._create_playlist_with_retries(name, description, public)
        except spotipy.SpotifyException:
            # Backoff decorator exhausted retries - return None gracefully
            return None

    @resilient_operation("create_spotify_playlist")
    @backoff.on_exception(
        backoff.expo,
        spotipy.SpotifyException,
        max_tries=3,
        giveup=should_giveup_on_error(SpotifyErrorClassifier()),
        on_backoff=create_backoff_handler(SpotifyErrorClassifier(), "spotify"),
        on_giveup=create_giveup_handler(SpotifyErrorClassifier(), "spotify"),
    )
    async def _create_playlist_with_retries(
        self, name: str, description: str = "", public: bool = False
    ) -> dict[str, Any] | None:
        """Internal implementation that allows SpotifyException to propagate to backoff decorator."""
        try:
            # Get current user ID
            user_info = await spotify_api_call(self.client, "me")
            user_id = user_info.get("id", "") if user_info else ""

            if not user_id:
                logger.error("Could not determine user ID for playlist creation")
                return None

            return await spotify_api_call(
                self.client,
                "user_playlist_create",
                user=user_id,
                name=name,
                public=public,
                description=description,
            )

        # NOTE: spotipy.SpotifyException exceptions are intentionally NOT caught here
        # They must propagate to the backoff decorator for proper retry logic

        except (ValueError, TypeError, AttributeError) as e:
            # Only catch non-retryable programming/parsing errors, not API errors
            logger.error(f"Failed to create playlist '{name}': {e}")
            return None

    # Playlist Modification API Methods

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
        except spotipy.SpotifyException:
            # Backoff decorator exhausted retries - return None gracefully
            return None

    @resilient_operation("add_spotify_playlist_items")
    @backoff.on_exception(
        backoff.expo,
        spotipy.SpotifyException,
        max_tries=3,
        giveup=should_giveup_on_error(SpotifyErrorClassifier()),
        on_backoff=create_backoff_handler(SpotifyErrorClassifier(), "spotify"),
        on_giveup=create_giveup_handler(SpotifyErrorClassifier(), "spotify"),
    )
    async def _playlist_add_items_with_retries(
        self, playlist_id: str, items: list[str], position: int | None = None
    ) -> dict[str, Any] | None:
        """Internal implementation that allows SpotifyException to propagate to backoff decorator."""
        return await spotify_api_call(
            self.client,
            "playlist_add_items",
            playlist_id=playlist_id,
            items=items,
            position=position,
        )

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
        except spotipy.SpotifyException:
            # Backoff decorator exhausted retries - return None gracefully
            return None

    @resilient_operation("remove_specific_spotify_playlist_items")
    @backoff.on_exception(
        backoff.expo,
        spotipy.SpotifyException,
        max_tries=3,
        giveup=should_giveup_on_error(SpotifyErrorClassifier()),
        on_backoff=create_backoff_handler(SpotifyErrorClassifier(), "spotify"),
        on_giveup=create_giveup_handler(SpotifyErrorClassifier(), "spotify"),
    )
    async def _playlist_remove_specific_occurrences_of_items_with_retries(
        self, playlist_id: str, items: list[dict], snapshot_id: str | None = None
    ) -> dict[str, Any] | None:
        """Internal implementation that allows SpotifyException to propagate to backoff decorator."""
        return await spotify_api_call(
            self.client,
            "playlist_remove_specific_occurrences_of_items",
            playlist_id=playlist_id,
            items=items,
            snapshot_id=snapshot_id,
        )

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
        except spotipy.SpotifyException:
            # Backoff decorator exhausted retries - return None gracefully
            return None

    @resilient_operation("reorder_spotify_playlist_items")
    @backoff.on_exception(
        backoff.expo,
        spotipy.SpotifyException,
        max_tries=3,
        giveup=should_giveup_on_error(SpotifyErrorClassifier()),
        on_backoff=create_backoff_handler(SpotifyErrorClassifier(), "spotify"),
        on_giveup=create_giveup_handler(SpotifyErrorClassifier(), "spotify"),
    )
    async def _playlist_reorder_items_with_retries(
        self,
        playlist_id: str,
        range_start: int,
        insert_before: int,
        range_length: int = 1,
        snapshot_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Internal implementation that allows SpotifyException to propagate to backoff decorator."""
        return await spotify_api_call(
            self.client,
            "playlist_reorder_items",
            playlist_id=playlist_id,
            range_start=range_start,
            insert_before=insert_before,
            range_length=range_length,
            snapshot_id=snapshot_id,
        )

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
        except spotipy.SpotifyException:
            # Backoff decorator exhausted retries - return None gracefully
            return None

    @resilient_operation("replace_spotify_playlist_items")
    @backoff.on_exception(
        backoff.expo,
        spotipy.SpotifyException,
        max_tries=3,
        giveup=should_giveup_on_error(SpotifyErrorClassifier()),
        on_backoff=create_backoff_handler(SpotifyErrorClassifier(), "spotify"),
        on_giveup=create_giveup_handler(SpotifyErrorClassifier(), "spotify"),
    )
    async def _playlist_replace_items_with_retries(
        self, playlist_id: str, items: list[str]
    ) -> dict[str, Any] | None:
        """Internal implementation that allows SpotifyException to propagate to backoff decorator."""
        return await spotify_api_call(
            self.client,
            "playlist_replace_items",
            playlist_id=playlist_id,
            items=items,
        )

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
    @backoff.on_exception(
        backoff.expo,
        spotipy.SpotifyException,
        max_tries=3,
        giveup=should_giveup_on_error(SpotifyErrorClassifier()),
        on_backoff=create_backoff_handler(SpotifyErrorClassifier(), "spotify"),
        on_giveup=create_giveup_handler(SpotifyErrorClassifier(), "spotify"),
    )
    async def _playlist_change_details_with_retries(
        self, playlist_id: str, name: str | None = None, description: str | None = None
    ) -> None:
        """Internal implementation that allows SpotifyException to propagate to backoff decorator."""
        kwargs = {}
        if name is not None:
            kwargs["name"] = name
        if description is not None:
            kwargs["description"] = description

        if kwargs:
            await spotify_api_call(
                self.client,
                "playlist_change_details",
                playlist_id=playlist_id,
                **kwargs,
            )

    # User Library API Methods

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
        except spotipy.SpotifyException:
            # Backoff decorator exhausted retries - return None gracefully
            return None

    @resilient_operation("get_spotify_saved_tracks")
    @backoff.on_exception(
        backoff.expo,
        spotipy.SpotifyException,
        max_tries=3,
        giveup=should_giveup_on_error(SpotifyErrorClassifier()),
        on_backoff=create_backoff_handler(SpotifyErrorClassifier(), "spotify"),
        on_giveup=create_giveup_handler(SpotifyErrorClassifier(), "spotify"),
    )
    async def _get_saved_tracks_with_retries(
        self, limit: int = 50, offset: int = 0
    ) -> dict[str, Any] | None:
        """Internal implementation that allows SpotifyException to propagate to backoff decorator."""
        return await spotify_api_call(
            self.client,
            "current_user_saved_tracks",
            limit=min(limit, 50),
            offset=offset,
            market=self.market,
        )

    async def get_current_user(self) -> dict[str, Any] | None:
        """Get current Spotify user information.

        Returns:
            User data if authenticated, None otherwise
        """
        try:
            return await self._get_current_user_with_retries()
        except spotipy.SpotifyException:
            # Backoff decorator exhausted retries - return None gracefully
            return None

    @resilient_operation("get_spotify_current_user")
    @backoff.on_exception(
        backoff.expo,
        spotipy.SpotifyException,
        max_tries=3,
        giveup=should_giveup_on_error(SpotifyErrorClassifier()),
        on_backoff=create_backoff_handler(SpotifyErrorClassifier(), "spotify"),
        on_giveup=create_giveup_handler(SpotifyErrorClassifier(), "spotify"),
    )
    async def _get_current_user_with_retries(self) -> dict[str, Any] | None:
        """Internal implementation that allows SpotifyException to propagate to backoff decorator."""
        return await spotify_api_call(self.client, "me")
