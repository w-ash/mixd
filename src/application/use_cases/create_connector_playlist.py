"""Creates playlists on external music services with internal database sync.

Handles the two-phase creation process: first creates the playlist on the external
service (Spotify, Apple Music), then optimistically syncs the result to the internal
database. Manages transaction boundaries and provides detailed operation results.
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
    """Input data for creating a playlist on an external music service.

    Contains the tracks, metadata, and configuration needed to create a playlist
    on services like Spotify or Apple Music, with optional internal sync.
    """

    tracklist: TrackList
    playlist_name: str
    connector: str  # "spotify", "apple_music", etc.
    playlist_description: str = "Created by Narada"
    create_internal_playlist: bool = True  # Whether to also create internal playlist
    metadata: dict[str, Any] = field(factory=dict)
    timestamp: datetime = field(factory=lambda: datetime.now(UTC))

    def validate(self) -> bool:
        """Checks if the command has valid data for playlist creation.

        Returns:
            True if the command contains valid tracks, playlist name, and connector.
        """
        logger.debug(
            "Validating CreateConnectorPlaylistCommand",
            has_tracks=bool(self.tracklist.tracks),
            track_count=len(self.tracklist.tracks) if self.tracklist.tracks else 0,
            has_playlist_name=bool(self.playlist_name),
            playlist_name=self.playlist_name,
            has_connector=bool(self.connector),
            connector=self.connector,
        )

        if not self.tracklist.tracks:
            logger.warning("Validation failed: no tracks in tracklist")
            return False

        if not self.playlist_name:
            logger.warning("Validation failed: missing playlist name")
            return False

        if not self.connector:
            logger.warning("Validation failed: missing connector")
            return False

        logger.debug("CreateConnectorPlaylistCommand validation passed")
        return True


@define(frozen=True, slots=True)
class CreateConnectorPlaylistResult:
    """Results and metadata from playlist creation on an external service.

    Includes the created playlist, external service IDs, performance metrics,
    and any errors that occurred during the operation.
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
        """Returns a summary of the playlist creation operation for logging."""
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
    """Creates playlists on external music services with optional internal sync.

    Orchestrates the two-phase process of creating playlists on external services
    (Spotify, Apple Music) and syncing the results to the internal database.
    Uses optimistic updates to maintain data consistency across systems.
    """

    async def execute(
        self, command: CreateConnectorPlaylistCommand, uow: UnitOfWorkProtocol
    ) -> CreateConnectorPlaylistResult:
        """Creates a playlist on the external service and optionally syncs to internal DB.

        First creates the playlist on the external service, then if successful and
        requested, creates a corresponding internal playlist with the same tracks.

        Args:
            command: Playlist creation parameters and configuration.
            uow: Database transaction manager and repository provider.

        Returns:
            Results including created playlist, external IDs, and operation metrics.

        Raises:
            ValueError: If the command fails validation checks.
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
                    connector_playlist_identifiers={
                        command.connector: external_result["playlist_id"]
                    },
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
                internal_playlist_id=internal_playlist.id
                if internal_playlist
                else None,
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
        """Creates the playlist on the external music service via its API.

        Uses the appropriate connector (Spotify, Apple Music) to create the playlist
        with the specified tracks and metadata on the external service.

        Args:
            command: Playlist creation parameters including tracks and metadata.
            uow: Provides access to the connector services.

        Returns:
            Dict with success status, external playlist ID, metadata, and any errors.
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
            if hasattr(connector, "get_playlist_metadata"):
                try:
                    connector_metadata = await connector.get_playlist_metadata(
                        external_playlist_id
                    )
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
        """Creates internal playlist based on successful external playlist creation.

        After external playlist creation succeeds, creates corresponding internal
        database records for the playlist and tracks. Uses optimistic updates
        assuming the external operation was successful.

        Args:
            command: Original playlist creation parameters.
            external_playlist_id: ID returned from successful external creation.
            external_metadata: Metadata from the external API response.
            uow: Database transaction manager and repository provider.

        Returns:
            The created internal playlist with persisted tracks.

        Raises:
            Exception: If internal playlist creation fails (triggers rollback).
        """
        async with uow:
            try:
                # Step 1: Ensure all tracks have database IDs via upsert
                track_repo = uow.get_track_repository()
                persisted_tracks = []

                for track in command.tracklist.tracks:
                    # Save track if it doesn't have an ID (not yet persisted)
                    if track.id is None:
                        try:
                            saved_track = await track_repo.save_track(track)
                            persisted_tracks.append(saved_track)
                        except Exception as e:
                            logger.warning(
                                f"Failed to persist track {track.title}: {e}"
                            )
                            persisted_tracks.append(
                                track
                            )  # Keep original if persist fails
                    else:
                        # Track already persisted, use as-is
                        persisted_tracks.append(track)

                # Step 2: Create internal playlist with connector mapping
                playlist = Playlist(
                    name=command.playlist_name,
                    description=command.playlist_description,
                    tracks=persisted_tracks,
                    connector_playlist_identifiers={
                        command.connector: external_playlist_id
                    },
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
        """Creates connector_playlist table entry to store external service metadata.

        Stores the mapping between internal and external playlists along with
        track positions and external service metadata for future sync operations.

        Args:
            saved_playlist: The internal playlist that was just created.
            external_playlist_id: ID from the external music service.
            external_metadata: Additional metadata from the external API.
            command: Original creation command for context.
            uow: Database transaction manager and repository provider.
        """
        try:
            connector_repo = uow.get_connector_playlist_repository()

            # Create track items list for connector_playlist table
            items = []
            for i, track in enumerate(saved_playlist.tracks):
                if (
                    track.connector_track_identifiers
                    and track.connector_track_identifiers.get(command.connector)
                ):
                    item = ConnectorPlaylistItem(
                        connector_track_identifier=track.connector_track_identifiers[
                            command.connector
                        ],
                        position=i,
                        added_at=datetime.now(UTC).isoformat(),
                        added_by_id="narada",
                        extras={
                            "track_uri": f"{command.connector}:track:{track.connector_track_identifiers[command.connector]}",
                            "local": False,
                            "primary_color": None,
                            "video_thumbnail": None,
                        },
                    )
                    items.append(item)

            # Create ConnectorPlaylist domain model
            connector_playlist = ConnectorPlaylist(
                connector_name=command.connector,
                connector_playlist_identifier=external_playlist_id,
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
