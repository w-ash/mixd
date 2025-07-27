"""CreateConnectorPlaylistUseCase for external service playlist creation.

This use case handles creation of new playlists on external services (like Spotify)
with proper orchestration between external API calls and internal database persistence,
following the optimistic update pattern for 2025 best practices.
"""

from datetime import UTC, datetime
from typing import Any

from attrs import define, field

from src.config import get_logger
from src.domain.entities import ConnectorPlaylist
from src.domain.entities.playlist import ConnectorPlaylistItem, Playlist
from src.domain.entities.track import TrackList
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class CreateConnectorPlaylistCommand:
    """Command for creating a new connector playlist.
    
    Encapsulates all information needed to create a playlist on an external
    service with proper internal database synchronization.
    """

    tracklist: TrackList
    playlist_name: str
    connector: str  # "spotify", "apple_music", etc.
    playlist_description: str = "Created by Narada"
    create_internal_playlist: bool = True  # Whether to also create internal playlist
    metadata: dict[str, Any] = field(factory=dict)
    timestamp: datetime = field(factory=lambda: datetime.now(UTC))

    def validate(self) -> bool:
        """Validate command business rules.

        Returns:
            True if command is valid for execution
        """
        if not self.tracklist.tracks:
            return False

        if not self.playlist_name:
            return False

        return bool(self.connector)


@define(frozen=True, slots=True)
class CreateConnectorPlaylistResult:
    """Result of connector playlist creation operation.

    Contains the created playlist, external service metadata, and performance
    metrics for monitoring and debugging purposes.
    """

    playlist: Playlist
    connector: str
    external_playlist_id: str
    tracks_created: int = 0
    execution_time_ms: int = 0
    external_metadata: dict[str, Any] = field(factory=dict)  # e.g., Spotify snapshot_id
    errors: list[str] = field(factory=list)

    @property
    def operation_summary(self) -> dict[str, Any]:
        """Summary of creation operation."""
        return {
            "playlist_id": self.playlist.id,
            "playlist_name": self.playlist.name,
            "connector": self.connector,
            "external_playlist_id": self.external_playlist_id,
            "tracks_created": self.tracks_created,
            "execution_time_ms": self.execution_time_ms,
            "success": len(self.errors) == 0,
        }


@define(slots=True)
class CreateConnectorPlaylistUseCase:
    """Use case for creating new external service playlists with internal sync.

    Handles external service playlist creation following Clean Architecture principles:
    - Creates playlist on external service (Spotify, Apple Music, etc.)
    - Uses optimistic update pattern to sync with internal database
    - Maintains proper transaction boundaries and error handling
    - No direct database modifications until external API succeeds
    """

    async def execute(
        self, command: CreateConnectorPlaylistCommand, uow: UnitOfWorkProtocol
    ) -> CreateConnectorPlaylistResult:
        """Execute connector playlist creation operation.

        Args:
            command: Command with playlist creation context
            uow: UnitOfWork for transaction management and repository access

        Returns:
            Result with creation status and operational metadata

        Raises:
            ValueError: If command validation fails
        """
        if not command.validate():
            raise ValueError("Invalid command: failed business rule validation")

        start_time = datetime.now(UTC)

        logger.info(
            "Starting connector playlist creation",
            connector=command.connector,
            playlist_name=command.playlist_name,
            track_count=len(command.tracklist.tracks),
            create_internal=command.create_internal_playlist,
        )

        try:
            # Step 1: Create playlist on external service
            external_result = await self._create_external_playlist(command, uow)

            # Step 2: Optimistic internal sync if requested and external succeeded
            internal_playlist = None
            if external_result["success"] and command.create_internal_playlist:
                internal_playlist = await self._create_internal_playlist_optimistic(
                    command=command,
                    external_playlist_id=external_result["playlist_id"],
                    external_metadata=external_result["metadata"],
                    uow=uow,
                )

            # Step 3: Calculate execution metrics
            execution_time = int(
                (datetime.now(UTC) - start_time).total_seconds() * 1000
            )

            # Use internal playlist if created, otherwise create minimal playlist for result
            if internal_playlist:
                result_playlist = internal_playlist
            else:
                # Create minimal playlist entity for result (not persisted)
                result_playlist = Playlist(
                    name=command.playlist_name,
                    description=command.playlist_description,
                    tracks=command.tracklist.tracks,
                    connector_playlist_ids={command.connector: external_result["playlist_id"]},
                )

            result = CreateConnectorPlaylistResult(
                playlist=result_playlist,
                connector=command.connector,
                external_playlist_id=external_result["playlist_id"],
                tracks_created=len(command.tracklist.tracks),
                execution_time_ms=execution_time,
                external_metadata=external_result["metadata"],
                errors=external_result.get("errors", []),
            )

            logger.info(
                "Connector playlist creation completed",
                connector=command.connector,
                external_playlist_id=external_result["playlist_id"],
                internal_playlist_id=internal_playlist.id if internal_playlist else None,
                tracks_created=result.tracks_created,
                execution_time_ms=execution_time,
            )

            return result

        except Exception as e:
            logger.error(
                "Connector playlist creation failed",
                error=str(e),
                connector=command.connector,
                playlist_name=command.playlist_name,
            )
            raise

    async def _create_external_playlist(
        self, command: CreateConnectorPlaylistCommand, uow: UnitOfWorkProtocol
    ) -> dict[str, Any]:
        """Create playlist on external service using real connector integration.

        Replaces simulation logic with actual external API calls via connector provider.
        
        Args:
            command: Creation command with configuration
            uow: UnitOfWork for accessing connector provider

        Returns:
            Dict with success status, playlist_id, metadata, and errors
        """
        try:
            # Get appropriate connector service (Spotify, Apple Music, etc.)
            connector_provider = uow.get_service_connector_provider()
            connector = connector_provider.get_connector(command.connector)
            
            # Create playlist via external API
            logger.info(
                "Creating external playlist via connector",
                connector=command.connector,
                playlist_name=command.playlist_name,
                tracks_count=len(command.tracklist.tracks),
            )
            
            external_playlist_id = await connector.create_playlist(
                name=command.playlist_name,
                tracks=command.tracklist.tracks,
                description=command.playlist_description,
            )
            
            # Build metadata response (format may vary by connector)
            external_metadata = {
                "created_at": datetime.now(UTC).isoformat(),
                "owner": "narada",
                "public": False,  # Default for privacy
                "collaborative": False,
                "follower_count": 0,
                "external_url": f"https://{command.connector}.com/playlist/{external_playlist_id}",
            }
            
            # Add connector-specific metadata if available
            if hasattr(connector, 'get_playlist_metadata'):
                try:
                    connector_metadata = await connector.get_playlist_metadata(external_playlist_id)
                    external_metadata.update(connector_metadata)
                except Exception as metadata_error:
                    logger.warning(
                        "Failed to retrieve connector metadata",
                        connector=command.connector,
                        playlist_id=external_playlist_id,
                        error=str(metadata_error),
                    )

            logger.info(
                "External playlist created successfully",
                connector=command.connector,
                external_playlist_id=external_playlist_id,
                tracks_count=len(command.tracklist.tracks),
            )

            return {
                "success": True,
                "playlist_id": external_playlist_id,
                "metadata": external_metadata,
                "errors": [],
            }

        except Exception as e:
            logger.error(
                "External playlist creation failed",
                connector=command.connector,
                error=str(e),
                error_type=type(e).__name__,
            )
            return {
                "success": False,
                "playlist_id": None,
                "metadata": {},
                "errors": [str(e)],
            }

    async def _create_internal_playlist_optimistic(
        self,
        command: CreateConnectorPlaylistCommand,
        external_playlist_id: str,
        external_metadata: dict[str, Any],
        uow: UnitOfWorkProtocol,
    ) -> Playlist:
        """Create internal playlist optimistically based on successful external creation.

        This implements the optimistic update pattern where we immediately create
        our internal database entities based on successful external API response.

        Args:
            command: Original creation command
            external_playlist_id: ID from successful external playlist creation
            external_metadata: Metadata from external API response
            uow: UnitOfWork for repository access

        Returns:
            Created internal playlist

        Raises:
            Exception: If internal playlist creation fails
        """
        async with uow:
            try:
                # Step 1: Ensure all tracks have database IDs via upsert
                track_repo = uow.get_track_repository()
                persisted_tracks = []
                
                for track in command.tracklist.tracks:
                    try:
                        saved_track = await track_repo.save_track(track)
                        persisted_tracks.append(saved_track)
                    except Exception as e:
                        logger.warning(f"Failed to persist track {track.title}: {e}")
                        persisted_tracks.append(track)  # Keep original if persist fails

                # Step 2: Create internal playlist with connector mapping
                playlist = Playlist(
                    name=command.playlist_name,
                    description=command.playlist_description,
                    tracks=persisted_tracks,
                    connector_playlist_ids={command.connector: external_playlist_id},
                )

                # Save internal playlist
                playlist_repo = uow.get_playlist_repository()
                saved_playlist = await playlist_repo.save_playlist(playlist)

                # Step 3: Create connector_playlist entry for metadata storage
                await self._create_connector_playlist_entry(
                    saved_playlist=saved_playlist,
                    external_playlist_id=external_playlist_id,
                    external_metadata=external_metadata,
                    command=command,
                    uow=uow,
                )

                # Step 4: Commit the transaction
                await uow.commit()

                logger.debug(
                    "Internal playlist created optimistically",
                    playlist_id=saved_playlist.id,
                    connector=command.connector,
                    external_playlist_id=external_playlist_id,
                    tracks_persisted=len(persisted_tracks),
                )

                return saved_playlist

            except Exception as e:
                # Rollback on any failure
                await uow.rollback()
                logger.error(
                    "Failed to create internal playlist after successful external creation",
                    connector=command.connector,
                    external_playlist_id=external_playlist_id,
                    error=str(e),
                )
                raise

    async def _create_connector_playlist_entry(
        self,
        saved_playlist: Playlist,
        external_playlist_id: str,
        external_metadata: dict[str, Any],
        command: CreateConnectorPlaylistCommand,
        uow: UnitOfWorkProtocol,
    ) -> None:
        """Create connector_playlist table entry for metadata storage.

        Args:
            saved_playlist: The saved internal playlist
            external_playlist_id: ID from external service
            external_metadata: Metadata from external API
            command: Original creation command
            uow: UnitOfWork for repository access
        """
        try:
            connector_repo = uow.get_connector_playlist_repository()
            
            # Create track items list for connector_playlist table
            items = []
            for i, track in enumerate(saved_playlist.tracks):
                if track.connector_track_ids and track.connector_track_ids.get(command.connector):
                    item = ConnectorPlaylistItem(
                        connector_track_id=track.connector_track_ids[command.connector],
                        position=i,
                        added_at=datetime.now(UTC).isoformat(),
                        added_by_id="narada",
                        extras={
                            "track_uri": f"{command.connector}:track:{track.connector_track_ids[command.connector]}",
                            "local": False,
                            "primary_color": None,
                            "video_thumbnail": None,
                        }
                    )
                    items.append(item)

            # Create ConnectorPlaylist domain model
            connector_playlist = ConnectorPlaylist(
                connector_name=command.connector,
                connector_playlist_id=external_playlist_id,
                name=command.playlist_name,
                description=command.playlist_description,
                owner=external_metadata.get("owner"),
                owner_id=external_metadata.get("owner_id"),
                is_public=external_metadata.get("public", False),
                collaborative=external_metadata.get("collaborative", False),
                follower_count=external_metadata.get("follower_count", 0),
                items=items,
                raw_metadata=external_metadata,
                last_updated=datetime.now(UTC),
            )

            # Save connector playlist entry
            await connector_repo.upsert_model(connector_playlist)

            logger.debug(
                "Connector playlist entry created",
                connector=command.connector,
                external_playlist_id=external_playlist_id,
                items_count=len(items),
            )

        except Exception as e:
            # Log but don't fail the entire operation - external creation succeeded
            logger.warning(
                "Failed to create connector_playlist entry after successful playlist creation",
                connector=command.connector,
                external_playlist_id=external_playlist_id,
                error=str(e),
                # This would be a good candidate for a compensation queue in production
            )