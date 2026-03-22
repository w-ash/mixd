"""Creates playlists on external music services with internal database sync.

Handles the two-phase creation process: first creates the playlist on the external
service (Spotify, Apple Music), then optimistically syncs the result to the internal
database. Manages transaction boundaries and provides detailed operation results.
"""

# pyright: reportAny=false

from datetime import UTC, datetime
from typing import Any, TypedDict

from attrs import define, field

from src.application.use_cases._shared import (
    create_connector_playlist_items_from_tracks,
    persist_unsaved_tracks,
    resolve_playlist_connector,
)
from src.application.use_cases._shared.command_validators import (
    non_empty_string,
    validate_tracklist_has_tracks,
)
from src.application.utilities.timing import ExecutionTimer
from src.config import get_logger
from src.domain.entities import ConnectorPlaylist, utc_now_factory
from src.domain.entities.playlist import Playlist
from src.domain.entities.track import TrackList
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


class _ExternalPlaylistResult(TypedDict):
    """Internal result shape from external playlist creation API call."""

    success: bool
    playlist_id: str | None
    # External API metadata is genuinely dynamic (varies by connector)
    metadata: dict[str, Any]  # pyright: ignore[reportExplicitAny]
    errors: list[str]


@define(frozen=True, slots=True)
class CreateConnectorPlaylistCommand:
    """Input data for creating a playlist on an external music service.

    Contains the tracks, metadata, and configuration needed to create a playlist
    on services like Spotify or Apple Music, with optional internal sync.
    """

    tracklist: TrackList = field(validator=validate_tracklist_has_tracks)
    playlist_name: str = field(validator=non_empty_string)
    connector: str = field(validator=non_empty_string)  # "spotify", "apple_music", etc.
    playlist_description: str = "Created by Mixd"
    create_internal_playlist: bool = True  # Whether to also create internal playlist
    metadata: dict[str, object] = field(factory=dict)
    timestamp: datetime = field(factory=utc_now_factory)


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
    # External API metadata is genuinely dynamic (varies by connector)
    external_metadata: dict[str, Any] = field(factory=dict)  # pyright: ignore[reportExplicitAny]
    errors: list[str] = field(factory=list)

    @property
    def operation_summary(self) -> dict[str, object]:
        """Returns a summary of the playlist creation operation for logging."""
        return {
            "playlist_id": self.playlist.id,
            "playlist_name": self.playlist.name,
            "connector": self.connector,
            "external_playlist_id": self.external_playlist_id,
            "tracks_created": self.tracks_created,
            "execution_time_ms": self.execution_time_ms,
            "success": not self.errors,
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
            ValueError: If the command execution fails.
        """
        timer = ExecutionTimer()

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
            external_playlist_id = external_result["playlist_id"]
            internal_playlist = None
            if (
                external_result["success"]
                and external_playlist_id is not None
                and command.create_internal_playlist
            ):
                internal_playlist = await self._create_internal_playlist_optimistic(
                    command=command,
                    external_playlist_id=external_playlist_id,
                    external_metadata=external_result["metadata"],
                    uow=uow,
                )

            # Use internal playlist if created, otherwise create minimal playlist for result
            if internal_playlist:
                result_playlist = internal_playlist
            elif external_playlist_id is not None:
                # Create minimal playlist entity for result (not persisted)
                result_playlist = Playlist.from_tracklist(
                    name=command.playlist_name,
                    tracklist=command.tracklist,
                    added_at=datetime.now(UTC),
                    description=command.playlist_description,
                    connector_playlist_identifiers={
                        command.connector: external_playlist_id
                    },
                )
            else:
                # External creation failed — create result without connector identifiers
                result_playlist = Playlist.from_tracklist(
                    name=command.playlist_name,
                    tracklist=command.tracklist,
                    added_at=datetime.now(UTC),
                    description=command.playlist_description,
                )

            result = CreateConnectorPlaylistResult(
                playlist=result_playlist,
                connector=command.connector,
                external_playlist_id=external_playlist_id or "",
                tracks_created=len(command.tracklist.tracks),
                execution_time_ms=timer.stop(),
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
                execution_time_ms=timer.elapsed_ms,
            )

        except Exception as e:
            logger.error(
                "Connector playlist creation failed",
                error=str(e),
                connector=command.connector,
                playlist_name=command.playlist_name,
            )
            raise
        else:
            return result

    async def _create_external_playlist(
        self, command: CreateConnectorPlaylistCommand, uow: UnitOfWorkProtocol
    ) -> _ExternalPlaylistResult:
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
            connector = resolve_playlist_connector(command.connector, uow)

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
            external_metadata: dict[str, Any] = {  # pyright: ignore[reportExplicitAny]
                "created_at": datetime.now(UTC).isoformat(),
                "owner": "mixd",
                "public": False,  # Default for privacy
                "collaborative": False,
                "follower_count": 0,
                "external_url": f"https://{command.connector}.com/playlist/{external_playlist_id}",
            }

            # Add connector-specific metadata if available (optional method)
            get_metadata = getattr(connector, "get_playlist_metadata", None)
            if get_metadata is not None:
                try:
                    connector_metadata = await get_metadata(external_playlist_id)
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
        else:
            return {
                "success": True,
                "playlist_id": external_playlist_id,
                "metadata": external_metadata,
                "errors": [],
            }

    async def _create_internal_playlist_optimistic(
        self,
        command: CreateConnectorPlaylistCommand,
        external_playlist_id: str,
        external_metadata: dict[str, Any],  # pyright: ignore[reportExplicitAny]
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
                try:
                    persisted_tracks = await persist_unsaved_tracks(
                        command.tracklist.tracks, uow
                    )
                except Exception as e:
                    logger.warning(f"Failed to persist some tracks: {e}")
                    persisted_tracks = list(command.tracklist.tracks)

                # Step 2: Create internal playlist with connector mapping
                tracklist = TrackList(tracks=persisted_tracks)
                playlist = Playlist.from_tracklist(
                    name=command.playlist_name,
                    tracklist=tracklist,
                    added_at=datetime.now(UTC),
                    description=command.playlist_description,
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
            else:
                return saved_playlist

    async def _create_connector_playlist_entry(
        self,
        saved_playlist: Playlist,
        external_playlist_id: str,
        external_metadata: dict[str, Any],  # pyright: ignore[reportExplicitAny]
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

            # Create track items list for connector_playlist table using factory
            items = create_connector_playlist_items_from_tracks(
                tracks=list(saved_playlist.tracks),
                connector_name=command.connector,
            )

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
            _ = await connector_repo.upsert_model(connector_playlist)

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
