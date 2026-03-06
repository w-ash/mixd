"""Spotify connector facade.

Provides the main SpotifyConnector class that implements the BaseAPIConnector
protocol while delegating to modular components. The facade pattern keeps a
single public interface while the internal implementation is split across
SpotifyAPIClient, SpotifyOperations, and conversion utilities.
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: API response dicts, connector facade delegation

from collections.abc import Awaitable, Callable
from typing import Any, ClassVar, override

from attrs import define, field

from src.config import get_logger, settings
from src.domain.entities import (
    ConnectorPlaylist,
    ConnectorTrack,
    Playlist,
    Track,
)
from src.domain.playlist.diff_engine import PlaylistOperation
from src.infrastructure.connectors.base import (
    BaseAPIConnector,
    BaseMetricResolver,
    register_metrics,
)
from src.infrastructure.connectors.protocols import ConnectorConfig
from src.infrastructure.connectors.spotify.client import SpotifyAPIClient
from src.infrastructure.connectors.spotify.error_classifier import (
    SpotifyErrorClassifier,
)
from src.infrastructure.connectors.spotify.models import SpotifyTrack
from src.infrastructure.connectors.spotify.operations import SpotifyOperations

# Track conversion registry removed - conversions handled directly in modules

# Get contextual logger with service binding
logger = get_logger(__name__).bind(service="spotify")


@define(slots=True)
class SpotifyConnector(BaseAPIConnector):
    """Spotify connector facade with modular architecture.

    Maintains backward compatibility by implementing the same public interface
    as the original SpotifyConnector while delegating to focused modular
    components for improved maintainability and testability.

    Components:
    - SpotifyAPIClient: Pure API wrapper for individual Spotify API calls
    - SpotifyOperations: Business logic for complex workflows and batch processing
    - Conversion utilities: Data transformation between Spotify and domain models

    All public methods preserve exact same signatures and behavior for
    backward compatibility across the codebase.
    """

    # Internal components (not exposed publicly)
    _client: SpotifyAPIClient = field(init=False, repr=False)
    _operations: SpotifyOperations = field(init=False, repr=False)

    @property
    @override
    def connector_name(self) -> str:
        """The name of this connector."""
        return "spotify"

    @property
    @override
    def error_classifier(self):
        """Get Spotify-specific error classifier."""
        return SpotifyErrorClassifier()

    @property
    def client(self):
        """Access to underlying Spotify client for compatibility."""
        return self._client

    def __attrs_post_init__(self) -> None:
        """Initialize modular components."""
        logger.debug("Initializing modular Spotify connector")

        # Initialize client and operations
        self._client = SpotifyAPIClient()
        self._operations = SpotifyOperations(self._client)

    async def aclose(self) -> None:
        """Close underlying API client."""
        await self._client.aclose()

    # Track Operations - Delegate to operations

    async def get_external_track_data(
        self,
        tracks: list[Track],
        _progress_callback: Callable[[int, int, str], Awaitable[None]] | None = None,
    ) -> dict[int, dict[str, Any]]:
        """Unified interface for retrieving complete Spotify track data (TrackMetadataConnector protocol).

        Extracts Spotify IDs from Track objects and returns complete Spotify track objects
        keyed by track.id. This standardizes the interface across all connectors.
        """
        # Extract Spotify IDs from tracks that have them
        spotify_mapped = [
            (track, track.connector_track_identifiers.get("spotify"))
            for track in tracks
            if track.id is not None and track.connector_track_identifiers.get("spotify")
        ]

        if not spotify_mapped:
            return {}

        # Get spotify IDs and call existing bulk method
        spotify_ids = [sid for _, sid in spotify_mapped if sid is not None]
        raw_metadata = await self._operations.get_tracks_by_ids(spotify_ids)

        # Map back to track.id format expected by the protocol (requires dict)
        return {
            track.id: raw_metadata[spotify_id].model_dump()
            for track, spotify_id in spotify_mapped
            if spotify_id in raw_metadata and track.id is not None
        }

    async def get_tracks_by_ids(self, track_ids: list[str]) -> dict[str, SpotifyTrack]:
        """Fetch multiple tracks from Spotify in bulk."""
        return await self._operations.get_tracks_by_ids(track_ids)

    async def search_by_isrc(self, isrc: str) -> SpotifyTrack | None:
        """Search for a track using ISRC identifier."""
        return await self._client.search_by_isrc(isrc)

    async def search_track(
        self, artist: str, title: str, limit: int = 5
    ) -> list[SpotifyTrack]:
        """Search for tracks by artist and title.

        Returns multiple candidates so callers can rank by similarity.
        """
        return await self._client.search_track(artist, title, limit)

    # Playlist Operations - Delegate to operations

    async def get_spotify_playlist(self, playlist_id: str) -> ConnectorPlaylist:
        """Fetch a Spotify playlist with its tracks."""
        return await self._operations.get_playlist_with_all_tracks(playlist_id)

    async def create_playlist(
        self,
        name: str,
        tracks: list[Track],
        description: str | None = None,
    ) -> str:
        """Create a new Spotify playlist with tracks."""
        return await self._operations.create_playlist_with_tracks(
            name, tracks, description
        )

    async def update_playlist(
        self,
        playlist_id: str,
        playlist: Playlist,
        replace: bool = True,
    ) -> None:
        """Update an existing Spotify playlist."""
        await self._operations.update_playlist_content(playlist_id, playlist, replace)

    async def execute_playlist_operations(
        self,
        playlist_id: str,
        operations: list[PlaylistOperation],
        snapshot_id: str | None = None,
        track_repo: Any = None,
    ) -> str | None:
        """Execute a list of differential playlist operations."""
        return await self._operations.execute_playlist_operations(
            playlist_id, operations, snapshot_id, track_repo
        )

    # User Library Operations - Delegate to operations

    async def get_liked_tracks(
        self,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[ConnectorTrack], str | None]:
        """Fetch user's saved/liked tracks from Spotify with pagination."""
        return await self._operations.get_liked_tracks_paginated(limit, cursor)

    # Additional Playlist Operations - Delegate to operations

    async def append_tracks_to_playlist(
        self, playlist_id: str, tracks: list[Track]
    ) -> dict[str, Any]:
        """Append tracks to an existing Spotify playlist."""
        return await self._operations.append_tracks_to_playlist(playlist_id, tracks)

    async def update_playlist_metadata(
        self, playlist_id: str, metadata_updates: dict[str, str]
    ) -> None:
        """Update Spotify playlist metadata (name, description)."""
        await self._operations.update_playlist_metadata(playlist_id, metadata_updates)

    async def get_playlist_details(self, playlist_id: str) -> dict[str, Any]:
        """Get basic Spotify playlist metadata."""
        return await self._operations.get_playlist_details(playlist_id)

    @override
    def convert_track_to_connector(self, track_data: dict[str, Any]) -> ConnectorTrack:
        """Convert Spotify track data to ConnectorTrack domain model."""
        from .conversions import convert_spotify_track_to_connector

        return convert_spotify_track_to_connector(track_data)


@define(frozen=True, slots=True)
class SpotifyMetricResolver(BaseMetricResolver):
    """Resolves Spotify metrics from persistence layer."""

    # Map metric names to connector metadata fields
    FIELD_MAP: ClassVar[dict[str, str]] = {
        "spotify_popularity": "popularity",
        "explicit_flag": "explicit",
    }

    # Connector name for database operations
    CONNECTOR: ClassVar[str] = "spotify"


def get_connector_config() -> ConnectorConfig:
    """Spotify connector configuration."""
    return {
        "dependencies": ["auth"],
        "factory": lambda _params: SpotifyConnector(),
        "metrics": SpotifyMetricResolver.FIELD_MAP,
    }


# Register all metric resolvers with freshness from settings
_spotify_freshness = dict.fromkeys(
    SpotifyMetricResolver.FIELD_MAP, settings.freshness.spotify_hours
)
register_metrics(
    SpotifyMetricResolver(), SpotifyMetricResolver.FIELD_MAP, _spotify_freshness
)
