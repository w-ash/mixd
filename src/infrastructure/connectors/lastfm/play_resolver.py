"""Last.fm-specific connector play resolver with metadata preservation.

Handles Last.fm's available metadata including MusicBrainz IDs, track URLs,
and Last.fm ecosystem integration data.
"""

from collections.abc import Callable
from typing import Any

from src.config import get_logger
from src.domain.entities import ConnectorTrackPlay, PlayRecord, TrackPlay
from src.domain.repositories import UnitOfWorkProtocol

from .track_resolution_service import LastfmTrackResolutionService

logger = get_logger(__name__)


class LastfmConnectorPlayResolver:
    """Last.fm-specific connector play resolver.

    Preserves Last.fm's available metadata:
    - MusicBrainz IDs for enhanced matching
    - Album information when available
    - Track URLs for Last.fm ecosystem integration
    - Love status and streamability flags
    """

    def __init__(
        self, lastfm_resolution_service: LastfmTrackResolutionService | None = None
    ):
        """Initialize with Last.fm resolution service."""
        self.lastfm_resolution_service = (
            lastfm_resolution_service or LastfmTrackResolutionService()
        )

    async def resolve_connector_plays(
        self,
        connector_plays: list[ConnectorTrackPlay],
        uow: UnitOfWorkProtocol,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> tuple[list[TrackPlay], dict[str, Any]]:
        """Resolve Last.fm connector plays using existing infrastructure."""
        if not connector_plays:
            return [], self._create_empty_metrics()

        # Step 1: Convert ConnectorTrackPlay objects to PlayRecord objects
        play_records = self._convert_connector_plays_to_play_records(connector_plays)

        # Step 2: Use existing LastfmTrackResolutionService
        (
            resolved_tracks,
            resolution_metrics,
        ) = await self.lastfm_resolution_service.resolve_plays_to_canonical_tracks(
            play_records, uow, progress_callback
        )

        # Step 3: Create TrackPlay objects with Last.fm metadata preservation
        track_plays = []
        filtering_stats = {
            "raw_plays": len(connector_plays),
            "accepted_plays": 0,
            "error_count": 0,
            "resolution_failures": [],
        }

        for connector_play, resolved_track in zip(
            connector_plays, resolved_tracks, strict=False
        ):
            if resolved_track is None or resolved_track.id is None:
                filtering_stats["error_count"] += 1
                failure_info = {
                    "track": f"{connector_play.artist_name} - {connector_play.track_name}",
                    "reason": "track_resolution_failed",
                }
                filtering_stats["resolution_failures"].append(failure_info)
                logger.warning(
                    f"WARNING: Track not resolved: {connector_play.artist_name} - {connector_play.track_name}"
                )
                continue

            # Create TrackPlay with Last.fm metadata preservation
            filtering_stats["accepted_plays"] += 1

            # Preserve Last.fm's available metadata
            context = {
                # Core track identification
                "track_name": connector_play.track_name,
                "artist_name": connector_play.artist_name,
                "album_name": connector_play.album_name,
                # Last.fm specific metadata
                "lastfm_track_url": connector_play.service_metadata.get(
                    "lastfm_track_url"
                ),
                "lastfm_artist_url": connector_play.service_metadata.get(
                    "lastfm_artist_url"
                ),
                "lastfm_album_url": connector_play.service_metadata.get(
                    "lastfm_album_url"
                ),
                # MusicBrainz IDs for enhanced matching
                "mbid": connector_play.service_metadata.get("mbid"),
                "artist_mbid": connector_play.service_metadata.get("artist_mbid"),
                "album_mbid": connector_play.service_metadata.get("album_mbid"),
                # Last.fm flags
                "streamable": connector_play.service_metadata.get("streamable"),
                "loved": connector_play.service_metadata.get("loved"),
                # Resolution tracking
                "resolution_method": "lastfm_connector_play_resolver",
                "architecture_version": "connector_plays_deferred_resolution",
                # Preserve any additional Last.fm metadata
                **{
                    k: v
                    for k, v in connector_play.service_metadata.items()
                    if k
                    not in [
                        "lastfm_track_url",
                        "lastfm_artist_url",
                        "lastfm_album_url",
                        "mbid",
                        "artist_mbid",
                        "album_mbid",
                        "streamable",
                        "loved",
                    ]
                },
            }

            track_play = TrackPlay(
                track_id=resolved_track.id,
                service="lastfm",
                played_at=connector_play.played_at,
                ms_played=connector_play.ms_played,  # Will be None for Last.fm
                context=context,
                import_timestamp=connector_play.import_timestamp,
                import_source=connector_play.import_source or "lastfm_api",
                import_batch_id=connector_play.import_batch_id,
            )

            track_plays.append(track_play)

        # Combine filtering stats with resolution metrics
        lastfm_metrics = {
            **filtering_stats,
            "new_tracks_count": resolution_metrics.get("new_tracks", 0),
            "updated_tracks_count": resolution_metrics.get("existing_mappings", 0),
            "spotify_enhanced_count": resolution_metrics.get("spotify_enhanced", 0),
        }

        logger.info(
            "Processed Last.fm connector plays with metadata preservation",
            total_plays=len(connector_plays),
            accepted_plays=filtering_stats["accepted_plays"],
            error_count=filtering_stats["error_count"],
            new_tracks=lastfm_metrics["new_tracks_count"],
            updated_tracks=lastfm_metrics["updated_tracks_count"],
            spotify_enhanced=lastfm_metrics["spotify_enhanced_count"],
        )

        return track_plays, lastfm_metrics

    def _convert_connector_plays_to_play_records(
        self, connector_plays: list[ConnectorTrackPlay]
    ) -> list[PlayRecord]:
        """Convert ConnectorTrackPlay objects to PlayRecord objects."""
        play_records = []

        for connector_play in connector_plays:
            play_record = PlayRecord(
                artist_name=connector_play.artist_name,
                track_name=connector_play.track_name,
                played_at=connector_play.played_at,
                service="lastfm",
                album_name=connector_play.album_name,
                ms_played=connector_play.ms_played,  # Will be None for Last.fm
                service_metadata=connector_play.service_metadata,
                api_page=connector_play.api_page,
                raw_data=connector_play.raw_data,
            )
            play_records.append(play_record)

        return play_records

    def _create_empty_metrics(self) -> dict[str, Any]:
        """Create empty metrics dictionary."""
        return {
            "raw_plays": 0,
            "accepted_plays": 0,
            "error_count": 0,
            "resolution_failures": [],
            "new_tracks_count": 0,
            "updated_tracks_count": 0,
            "spotify_enhanced_count": 0,
        }
