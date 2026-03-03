"""Spotify-specific inward track resolver.

Resolves Spotify track IDs to canonical tracks using the shared
InwardTrackResolver pattern. Handles Spotify relinking (market-specific
ID changes) by creating both primary and secondary connector mappings.
"""

from typing import override

from src.config import get_logger
from src.domain.entities import Track
from src.domain.repositories import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.inward_track_resolver import (
    InwardTrackResolver,
)
from src.infrastructure.connectors.spotify import SpotifyConnector

from .utilities import create_track_from_spotify_data

logger = get_logger(__name__)


class SpotifyInwardResolver(InwardTrackResolver):
    """Resolves Spotify track IDs → canonical tracks.

    Uses Spotify's batch API (get_tracks_by_ids, up to 50 at once)
    for efficient creation of missing tracks.
    """

    _spotify_connector: SpotifyConnector

    def __init__(self, spotify_connector: SpotifyConnector):
        self._spotify_connector = spotify_connector

    @property
    @override
    def connector_name(self) -> str:
        return "spotify"

    @override
    def _normalize_id(self, raw_id: str) -> str:
        # Spotify IDs are stable alphanumeric strings — no normalization needed
        return raw_id

    @override
    async def _create_tracks_batch(
        self,
        missing_ids: list[str],
        uow: UnitOfWorkProtocol,
    ) -> dict[str, Track]:
        """Fetch metadata from Spotify API in batch, create tracks + mappings."""
        spotify_metadata = await self._spotify_connector.get_tracks_by_ids(missing_ids)

        result: dict[str, Track] = {}
        for spotify_id in missing_ids:
            if spotify_id not in spotify_metadata:
                logger.warning(f"No Spotify metadata for {spotify_id}")
                continue

            try:
                spotify_track = spotify_metadata[spotify_id]
                track_data = create_track_from_spotify_data(spotify_id, spotify_track)
                canonical_track = await uow.get_track_repository().save_track(
                    track_data
                )

                # Handle relinking: API response ID vs requested ID
                primary_id = spotify_track.id or spotify_id
                linked_from = spotify_track.linked_from

                # Primary mapping (market-appropriate ID from API response)
                _ = await uow.get_connector_repository().map_track_to_connector(
                    canonical_track,
                    "spotify",
                    primary_id,
                    "direct_import",
                    confidence=100,
                    metadata=spotify_track.model_dump(),
                    auto_set_primary=True,
                )

                # Secondary mapping for relinked IDs
                if linked_from and linked_from.id and spotify_id != primary_id:
                    logger.debug(
                        f"Handling Spotify relinking: {linked_from.id} -> {primary_id}"
                    )
                    _ = await uow.get_connector_repository().map_track_to_connector(
                        canonical_track,
                        "spotify",
                        linked_from.id,
                        "direct_import",
                        confidence=100,
                        metadata={"id": linked_from.id},
                        auto_set_primary=False,
                    )

                result[spotify_id] = canonical_track

            except Exception as e:
                logger.error(f"Failed to create track for {spotify_id}: {e}")

        return result
