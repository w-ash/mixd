"""CreateCanonicalPlaylistUseCase for pure internal database playlist creation.

This use case handles the creation of canonical (internal) playlists without
external service dependencies or complex enrichment. It focuses purely on
database operations following Clean Architecture principles.
"""

from datetime import UTC, datetime
from typing import Any

from attrs import define, field

from src.application.services.metrics_application_service import (
    MetricsApplicationService,
)
from src.config import get_logger
from src.domain.entities.playlist import Playlist
from src.domain.entities.track import Track, TrackList
from src.domain.repositories import UnitOfWorkProtocol
from src.infrastructure.connectors.metrics_config import (
    CONNECTOR_METRICS,
    FIELD_MAPPINGS,
)

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class CreateCanonicalPlaylistCommand:
    """Command for creating a canonical playlist.
    
    Encapsulates all information needed to create an internal playlist
    with tracks and metadata.
    """

    name: str
    tracklist: TrackList
    description: str | None = None
    metadata: dict[str, Any] = field(factory=dict)
    timestamp: datetime = field(factory=lambda: datetime.now(UTC))

    def validate(self) -> bool:
        """Validate command business rules.

        Returns:
            True if command is valid for execution
        """
        if not self.name.strip():
            return False

        return bool(self.tracklist.tracks)


@define(frozen=True, slots=True)
class CreateCanonicalPlaylistResult:
    """Result of canonical playlist creation operation.

    Contains the created playlist and operation metadata for monitoring
    and debugging purposes.
    """

    playlist: Playlist
    tracks_created: int = 0
    execution_time_ms: int = 0
    errors: list[str] = field(factory=list)

    @property
    def operation_summary(self) -> dict[str, Any]:
        """Summary of the creation operation."""
        return {
            "playlist_id": self.playlist.id,
            "playlist_name": self.playlist.name,
            "tracks_created": self.tracks_created,
            "execution_time_ms": self.execution_time_ms,
            "success": len(self.errors) == 0,
        }


@define(slots=True)
class CreateCanonicalPlaylistUseCase:
    """Use case for creating canonical (internal) playlists.

    Handles pure database operations for playlist creation following
    Clean Architecture principles with UnitOfWork pattern:
    - No constructor dependencies (pure domain layer)
    - All repository access through UnitOfWork parameter
    - Explicit transaction control in business logic
    - Simplified testing with single UnitOfWork mock
    """

    metrics_service: MetricsApplicationService = field(factory=MetricsApplicationService)

    async def execute(
        self, command: CreateCanonicalPlaylistCommand, uow: UnitOfWorkProtocol
    ) -> CreateCanonicalPlaylistResult:
        """Execute canonical playlist creation operation.

        Args:
            command: Command with playlist creation context
            uow: UnitOfWork for transaction management and repository access

        Returns:
            Result with created playlist and operational metadata

        Raises:
            ValueError: If command validation fails
        """
        if not command.validate():
            raise ValueError("Invalid command: failed business rule validation")

        start_time = datetime.now(UTC)

        logger.info(
            "Starting canonical playlist creation",
            name=command.name,
            track_count=len(command.tracklist.tracks),
        )

        async with uow:
            try:
                # Step 1: Ensure all tracks are persisted
                track_repo = uow.get_track_repository()
                persisted_tracks = []
                
                for track in command.tracklist.tracks:
                    # Save track if it doesn't have an ID (not yet persisted)
                    if track.id is None:
                        saved_track = await track_repo.save_track(track)
                        persisted_tracks.append(saved_track)
                    else:
                        persisted_tracks.append(track)

                # Step 2: Create playlist entity with optional connector mapping
                connector_playlist_ids = {}
                if command.metadata and "connector" in command.metadata and "connector_id" in command.metadata:
                    # Create connector mapping from metadata
                    connector_playlist_ids[command.metadata["connector"]] = command.metadata["connector_id"]
                    logger.info(
                        "Creating canonical playlist with connector mapping",
                        connector=command.metadata["connector"],
                        connector_id=command.metadata["connector_id"]
                    )
                
                playlist = Playlist(
                    name=command.name,
                    tracks=persisted_tracks,
                    description=command.description,
                    connector_playlist_ids=connector_playlist_ids,
                    metadata=command.metadata.copy() if command.metadata else {},
                )

                # Step 3: Persist playlist
                playlist_repo = uow.get_playlist_repository()
                saved_playlist = await playlist_repo.save_playlist(playlist)

                # Step 4: Extract metrics from connector metadata
                await self._extract_track_metrics(persisted_tracks, uow)

                # Step 5: Commit transaction
                await uow.commit()

                # Step 6: Calculate execution metrics
                execution_time = int(
                    (datetime.now(UTC) - start_time).total_seconds() * 1000
                )

                result = CreateCanonicalPlaylistResult(
                    playlist=saved_playlist,
                    tracks_created=len(persisted_tracks),
                    execution_time_ms=execution_time,
                )

                logger.info(
                    "Canonical playlist creation completed",
                    playlist_id=saved_playlist.id,
                    name=saved_playlist.name,
                    tracks_created=len(persisted_tracks),
                    execution_time_ms=execution_time,
                )

                return result

            except Exception as e:
                # Explicit rollback on business logic failure
                await uow.rollback()
                logger.error(
                    "Canonical playlist creation failed",
                    error=str(e),
                    name=command.name,
                    track_count=len(command.tracklist.tracks),
                )
                raise

    async def _extract_track_metrics(
        self, tracks: list["Track"], uow: UnitOfWorkProtocol
    ) -> None:
        """Extract metrics from connector metadata for all tracks.
        
        Args:
            tracks: List of tracks to extract metrics for
            uow: UnitOfWork for transaction management
        """
        if not tracks:
            return

        # Group tracks by connector to batch process metadata
        for connector, available_metrics in CONNECTOR_METRICS.items():
            # Find tracks that have metadata for this connector
            tracks_with_metadata = []
            fresh_metadata = {}
            
            for track in tracks:
                if (track.id and track.connector_metadata 
                    and connector in track.connector_metadata):
                    tracks_with_metadata.append(track)
                    fresh_metadata[track.id] = track.connector_metadata[connector]
            
            if fresh_metadata:
                logger.info(
                    f"Extracting {len(available_metrics)} metrics from {connector} for {len(tracks_with_metadata)} tracks",
                    connector=connector,
                    metrics=available_metrics,
                    track_count=len(tracks_with_metadata),
                )
                
                # Use the metrics service to batch process the fresh metadata
                await self.metrics_service.batch_process_fresh_metadata(
                    fresh_metadata=fresh_metadata,
                    connector=connector,
                    available_metrics=available_metrics,
                    field_map=FIELD_MAPPINGS,
                    uow=uow,
                )