"""Create internal playlists with tracks and persist them to the database.

Handles playlist creation workflow: validates tracks, persists missing tracks,
creates playlist entity, extracts metrics from connector metadata, and commits
the transaction. Returns operational metrics for monitoring.
"""

from collections.abc import Mapping
from datetime import datetime

from attrs import define, evolve, field

from src.application.services.metrics_application_service import (
    MetricsApplicationService,
)
from src.application.use_cases._shared.command_validators import non_empty_string
from src.application.use_cases._shared.metric_config import MetricConfigProvider
from src.application.use_cases._shared.timed_execution import timed_query
from src.application.utilities.timing import ExecutionTimer
from src.config import get_logger
from src.domain.entities import utc_now_factory
from src.domain.entities.playlist import ConnectorPlaylist, Playlist
from src.domain.entities.shared import JsonValue, empty_json_map
from src.domain.entities.track import TrackList
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class CreateCanonicalPlaylistCommand:
    """Input data for creating a playlist with tracks and metadata.

    Args:
        name: Playlist display name
        tracklist: Collection of tracks to include
        connector_playlist: Optional pre-fetched connector playlist data
        connector_name: Source connector name (e.g., "spotify") for playlist mapping
        connector_id: External playlist ID on the source connector
        description: Optional playlist description
        metadata: Additional key-value data passed through to Playlist.metadata
        timestamp: Creation timestamp (defaults to now)
    """

    user_id: str
    name: str = field(validator=non_empty_string)
    tracklist: TrackList = field(factory=TrackList)
    connector_playlist: ConnectorPlaylist | None = None
    connector_name: str | None = None
    connector_id: str | None = None
    description: str | None = None
    metadata: Mapping[str, JsonValue] = field(factory=empty_json_map)
    timestamp: datetime = field(factory=utc_now_factory)


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

    metric_config: MetricConfigProvider
    metrics_service: MetricsApplicationService = field(init=False)

    def __attrs_post_init__(self) -> None:
        self.metrics_service = MetricsApplicationService(
            metric_config=self.metric_config
        )

    def _build_connector_identifiers(
        self,
        command: CreateCanonicalPlaylistCommand,
        existing: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Build connector playlist identifiers from typed command fields."""
        identifiers = dict(existing) if existing else {}
        if command.connector_name is not None and command.connector_id is not None:
            identifiers[command.connector_name] = command.connector_id
            logger.info(
                "Creating canonical playlist with connector mapping",
                connector=command.connector_name,
                connector_id=command.connector_id,
            )
        return identifiers

    async def execute(
        self,
        command: CreateCanonicalPlaylistCommand,
        uow: UnitOfWorkProtocol,
        *,
        commit: bool = True,
    ) -> CreateCanonicalPlaylistResult:
        """Create playlist with tracks and persist to database.

        Args:
            command: Playlist creation parameters and track data
            uow: Transaction manager and repository provider
            commit: When True (default) this use case owns a complete
                transaction — opens its own UoW context and commits. A batch
                caller (import) passes ``commit=False`` to fold this write into
                its own per-item transaction: no nested ``async with`` (which
                would auto-commit the shared session on exit) and no commit, so
                the boundary — including rollback on failure — stays with the
                caller.

        Returns:
            Result containing created playlist and operation metrics

        Raises:
            ValueError: If command execution fails
        """
        logger.info(
            "Starting canonical playlist creation",
            name=command.name,
            track_count=len(command.tracklist.tracks),
            has_connector_playlist=command.connector_playlist is not None,
        )

        if not commit:
            # Preview path: bespoke timing, no failure envelope (previews
            # surface their own errors), no transaction to roll back.
            return await self._create_playlist(
                command, uow, ExecutionTimer(), commit=False
            )

        async with (
            uow,  # __aexit__ rolls back on exception
            timed_query(
                "Canonical playlist creation",
                error_log_context={
                    "name": command.name,
                    "track_count": len(command.tracklist.tracks),
                },
            ) as timer,
        ):
            return await self._create_playlist(command, uow, timer)

    async def _create_playlist(
        self,
        command: CreateCanonicalPlaylistCommand,
        uow: UnitOfWorkProtocol,
        timer: ExecutionTimer,
        *,
        commit: bool = True,
    ) -> CreateCanonicalPlaylistResult:
        """Builds the playlist from source data, persists it, and builds the result."""
        # Step 1: Process ConnectorPlaylist data if present (returns Playlist or TrackList)
        source_data = command.tracklist
        if command.connector_playlist is not None:
            from src.application.services.connector_playlist_processing_service import (
                ConnectorPlaylistProcessingService,
            )

            processing_service = ConnectorPlaylistProcessingService()
            source_data = await processing_service.process_connector_playlist(
                command.connector_playlist, uow, user_id=command.user_id
            )

        # Step 2: Handle both Playlist (with entries) and TrackList (tracks only) inputs
        if isinstance(source_data, Playlist):
            # The processing service already ingested tracks and built the
            # complete ordered entries — resolved tracks AND unresolved positions
            # carrying their connector ref. Use them as-is; a rebuild here would
            # drop the ref and lose every unmatched position.
            connector_playlist_identifiers = self._build_connector_identifiers(
                command,
                existing=source_data.connector_playlist_identifiers,
            )

            playlist = Playlist(
                name=command.name,
                user_id=command.user_id,
                entries=source_data.entries,
                description=command.description,
                connector_playlist_identifiers=connector_playlist_identifiers,
                metadata=dict(command.metadata) if command.metadata else {},
            )
        else:
            # TrackList input - convert to Playlist with uniform added_at
            connector_playlist_identifiers = self._build_connector_identifiers(command)

            playlist = Playlist.from_tracklist(
                name=command.name,
                tracklist=source_data,
                added_at=command.timestamp,
                description=command.description,
                connector_playlist_identifiers=connector_playlist_identifiers or {},
            )
            playlist = evolve(playlist, user_id=command.user_id)
            # Add metadata if provided
            if command.metadata:
                playlist = evolve(playlist, metadata=dict(command.metadata))

        # Step 3: Persist playlist
        playlist_repo = uow.get_playlist_repository()
        saved_playlist = await playlist_repo.save_playlist(playlist)

        # Step 4: Extract metrics from connector metadata (extract tracks from playlist)
        await self.metrics_service.extract_track_metrics(playlist.tracks, uow)

        # Step 5: Commit transaction (caller owns the boundary when commit=False)
        if commit:
            await uow.commit()

        # Step 6: Count unique tracks in the final playlist
        unique_track_count = len({
            track.id for track in saved_playlist.tracks if track.id
        })

        result = CreateCanonicalPlaylistResult(
            playlist=saved_playlist,
            tracks_created=unique_track_count,
            execution_time_ms=timer.stop(),
        )

        logger.info(
            "Canonical playlist creation completed",
            playlist_id=saved_playlist.id,
            name=saved_playlist.name,
            tracks_created=unique_track_count,
            execution_time_ms=timer.elapsed_ms,
        )

        return result
