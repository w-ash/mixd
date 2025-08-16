"""Create internal playlists with tracks and persist them to the database.

Handles playlist creation workflow: validates tracks, persists missing tracks,
creates playlist entity, extracts metrics from connector metadata, and commits
the transaction. Returns operational metrics for monitoring.
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
from src.infrastructure.connectors._shared.metrics import (
    get_all_connectors_metrics,
    get_all_field_mappings,
)

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class CreateCanonicalPlaylistCommand:
    """Input data for creating a playlist with tracks and metadata.

    Args:
        name: Playlist display name
        tracklist: Collection of tracks to include
        description: Optional playlist description
        metadata: Additional key-value data (connector IDs, etc.)
        timestamp: Creation timestamp (defaults to now)
    """

    name: str
    tracklist: TrackList
    description: str | None = None
    metadata: dict[str, Any] = field(factory=dict)
    timestamp: datetime = field(factory=lambda: datetime.now(UTC))

    def validate(self) -> bool:
        """Check if command has required data for playlist creation.

        Returns:
            True if name is non-empty and tracklist contains tracks or ConnectorPlaylist metadata
        """
        if not self.name.strip():
            return False

        # Accept either tracks OR ConnectorPlaylist metadata for processing
        has_tracks = bool(self.tracklist.tracks)
        has_connector_playlist = bool(
            self.tracklist.metadata
            and self.tracklist.metadata.get("connector_playlist")
        )

        return has_tracks or has_connector_playlist


@define(frozen=True, slots=True)
class CreateCanonicalPlaylistResult:
    """Output data from playlist creation operation.

    Args:
        playlist: The created and persisted playlist
        tracks_created: Number of new tracks saved to database
        execution_time_ms: Operation duration in milliseconds
        errors: List of error messages (empty on success)
    """

    playlist: Playlist
    tracks_created: int = 0
    execution_time_ms: int = 0
    errors: list[str] = field(factory=list)

    @property
    def operation_summary(self) -> dict[str, Any]:
        """Key metrics from the playlist creation operation."""
        return {
            "playlist_id": self.playlist.id,
            "playlist_name": self.playlist.name,
            "tracks_created": self.tracks_created,
            "execution_time_ms": self.execution_time_ms,
            "success": len(self.errors) == 0,
        }


@define(slots=True)
class CreateCanonicalPlaylistUseCase:
    """Creates playlists by persisting tracks and extracting metrics.

    Workflow:
    1. Validates input command
    2. Saves any new tracks to database
    3. Creates playlist with track references
    4. Extracts metrics from connector metadata
    5. Commits transaction and returns result

    All operations use provided UnitOfWork for transaction management.
    """

    metrics_service: MetricsApplicationService = field(
        factory=MetricsApplicationService
    )

    async def execute(
        self, command: CreateCanonicalPlaylistCommand, uow: UnitOfWorkProtocol
    ) -> CreateCanonicalPlaylistResult:
        """Create playlist with tracks and persist to database.

        Args:
            command: Playlist creation parameters and track data
            uow: Transaction manager and repository provider

        Returns:
            Result containing created playlist and operation metrics

        Raises:
            ValueError: If command validation fails (empty name/tracklist)
        """
        if not command.validate():
            raise ValueError("Invalid command: failed business rule validation")

        start_time = datetime.now(UTC)

        logger.info(
            "Starting canonical playlist creation",
            name=command.name,
            track_count=len(command.tracklist.tracks),
            has_connector_playlist=bool(
                command.tracklist.metadata
                and command.tracklist.metadata.get("connector_playlist")
            ),
        )

        async with uow:
            try:
                # Step 1: Process ConnectorPlaylist data if present
                processed_tracklist = command.tracklist
                if command.tracklist.metadata and command.tracklist.metadata.get(
                    "connector_playlist"
                ):
                    from src.application.services.connector_playlist_processing_service import (
                        ConnectorPlaylistProcessingService,
                    )

                    processing_service = ConnectorPlaylistProcessingService()
                    processed_tracklist = (
                        await processing_service.process_connector_playlist(
                            command.tracklist.metadata["connector_playlist"], uow
                        )
                    )

                # Step 2: Ensure all tracks are persisted
                track_repo = uow.get_track_repository()
                persisted_tracks = []

                for track in processed_tracklist.tracks:
                    # Save track if it doesn't have an ID (not yet persisted)
                    if track.id is None:
                        saved_track = await track_repo.save_track(track)
                        persisted_tracks.append(saved_track)
                    else:
                        persisted_tracks.append(track)

                # Step 2: Create playlist entity with optional connector mapping
                connector_playlist_ids = {}
                if (
                    command.metadata
                    and "connector" in command.metadata
                    and "connector_id" in command.metadata
                ):
                    # Create connector mapping from metadata
                    connector_playlist_ids[command.metadata["connector"]] = (
                        command.metadata["connector_id"]
                    )
                    logger.info(
                        "Creating canonical playlist with connector mapping",
                        connector=command.metadata["connector"],
                        connector_id=command.metadata["connector_id"],
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
        """Extract and save metrics from track connector metadata.

        Groups tracks by connector type and batch processes their metadata
        to extract structured metrics for analysis.

        Args:
            tracks: Tracks with potential connector metadata
            uow: Transaction manager for database operations
        """
        if not tracks:
            return

        # Group tracks by connector to batch process metadata
        for connector, available_metrics in get_all_connectors_metrics().items():
            # Find tracks that have metadata for this connector
            tracks_with_metadata = []
            fresh_metadata = {}

            for track in tracks:
                if (
                    track.id
                    and track.connector_metadata
                    and connector in track.connector_metadata
                ):
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
                    field_map=get_all_field_mappings(),
                    uow=uow,
                )
