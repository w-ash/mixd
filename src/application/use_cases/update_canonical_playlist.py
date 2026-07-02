"""Updates internal playlists by adding, removing, or reordering tracks.

Manages playlist modifications in the local database without syncing to external
music services. Supports both append mode (add tracks to end) and differential
mode (calculate minimal changes to transform current playlist into target state).
"""

from datetime import UTC, datetime

from attrs import define, evolve, field

from src.application.services.metrics_application_service import (
    MetricsApplicationService,
)
from src.application.use_cases._shared import (
    OperationCounts,
    build_playlist_changes,
    count_operation_types,
)
from src.application.use_cases._shared.command_validators import non_empty_string
from src.application.use_cases._shared.metric_config import MetricConfigProvider
from src.application.use_cases._shared.playlist_resolver import require_playlist
from src.application.utilities.timing import ExecutionTimer
from src.config import get_logger
from src.domain.entities import utc_now_factory
from src.domain.entities.playlist import ConnectorPlaylist, Playlist
from src.domain.entities.track import TrackList
from src.domain.playlist import (
    PlaylistDiff,
    calculate_playlist_diff,
    select_appendable_entries,
)
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class UpdateCanonicalPlaylistCommand:
    """Command containing all data needed to update a playlist.

    Args:
        playlist_id: Internal database ID of playlist to modify
        new_tracklist: Target tracks the playlist should contain after update
        dry_run: If True, calculate changes but don't save to database
        append_mode: If True, add tracks to end; if False, replace entire playlist
        playlist_name: New name for playlist (optional)
        playlist_description: New description for playlist (optional)
        metadata: Additional custom metadata to store
        timestamp: When this command was created
    """

    user_id: str
    playlist_id: str = field(validator=non_empty_string)
    new_tracklist: TrackList = field(factory=TrackList)
    connector_playlist: ConnectorPlaylist | None = None
    dry_run: bool = False
    append_mode: bool = False  # True=append, False=overwrite with preservation
    playlist_name: str | None = None  # Optional name update
    playlist_description: str | None = None  # Optional description update
    metadata: dict[str, object] = field(factory=dict)
    timestamp: datetime = field(factory=utc_now_factory)


@define(frozen=True, slots=True)
class UpdateCanonicalPlaylistResult:
    """Results from a playlist update operation.

    Attributes:
        playlist: The updated playlist entity
        operations_performed: Total number of database operations executed
        tracks_added: Number of tracks added to playlist
        tracks_removed: Number of tracks removed from playlist
        tracks_moved: Number of tracks that changed position
        execution_time_ms: How long the operation took in milliseconds
        confidence_score: How confident the diff algorithm was (0.0-1.0)
        errors: List of error messages if any operations failed
    """

    playlist: Playlist
    operations_performed: int = 0
    operation_counts: OperationCounts = field(factory=OperationCounts)
    execution_time_ms: int = 0
    confidence_score: float = 1.0
    playlist_changes: dict[str, object] = field(factory=dict)
    errors: list[str] = field(factory=list)

    @property
    def tracks_added(self) -> int:
        """Number of tracks added (from operation_counts)."""
        return self.operation_counts.added

    @property
    def tracks_removed(self) -> int:
        """Number of tracks removed (from operation_counts)."""
        return self.operation_counts.removed


@define(slots=True)
class UpdateCanonicalPlaylistUseCase:
    """Updates playlists stored in the local database.

    Handles two update modes:
    1. Append mode: Adds new tracks to the end of existing playlist
    2. Differential mode: Calculates minimal changes to transform current
       playlist into target state, preserving as many existing tracks as possible

    Also extracts music metadata from track connector data for analytics.
    """

    metric_config: MetricConfigProvider
    metrics_service: MetricsApplicationService = field(init=False)

    def __attrs_post_init__(self) -> None:
        self.metrics_service = MetricsApplicationService(
            metric_config=self.metric_config
        )

    async def execute(
        self,
        command: UpdateCanonicalPlaylistCommand,
        uow: UnitOfWorkProtocol,
        *,
        commit: bool = True,
    ) -> UpdateCanonicalPlaylistResult:
        """Updates a playlist with new tracks and optionally new metadata.

        Args:
            command: Contains playlist ID, target tracks, and update options
            uow: Database transaction manager and repository access

        Returns:
            Result containing updated playlist and operation statistics

        Raises:
            ValueError: If playlist ID is invalid or command execution fails
        """
        timer = ExecutionTimer()

        logger.info(
            "Starting canonical playlist update",
            playlist_id=command.playlist_id,
            track_count=len(command.new_tracklist.tracks),
            dry_run=command.dry_run,
        )

        if not commit:
            return await self._update_playlist(command, uow, timer, commit=False)

        async with uow:
            try:
                result = await self._update_playlist(command, uow, timer)
            except Exception as e:
                # Explicit rollback on business logic failure
                await uow.rollback()
                logger.error(
                    "Canonical playlist update failed",
                    error=str(e),
                    playlist_id=command.playlist_id,
                )
                raise
            else:
                return result

    async def _update_playlist(
        self,
        command: UpdateCanonicalPlaylistCommand,
        uow: UnitOfWorkProtocol,
        timer: ExecutionTimer,
        *,
        commit: bool = True,
    ) -> UpdateCanonicalPlaylistResult:
        """Applies the requested metadata/track updates and builds the result."""
        # Step 1: Resolve current state + the target playlist to reconcile against.
        current_playlist = await require_playlist(
            command.playlist_id, uow, user_id=command.user_id
        )
        processed_playlist = await self._resolve_target(command, current_playlist, uow)

        # Step 2: Handle metadata updates (name/description)
        if (
            command.playlist_name is not None
            or command.playlist_description is not None
        ):
            current_playlist = await self._update_playlist_metadata(
                current_playlist, command, uow
            )

        # Short-circuit: metadata-only update (no tracks, no connector playlist)
        has_tracks = bool(command.new_tracklist.tracks)
        has_connector = command.connector_playlist is not None
        if not has_tracks and not has_connector:
            if commit and not command.dry_run:
                await uow.commit()
            return UpdateCanonicalPlaylistResult(
                playlist=current_playlist,
                execution_time_ms=timer.stop(),
            )

        # Step 3: Handle track updates based on mode
        playlist_changes: dict[str, object] = {}
        if command.append_mode:
            # Append mode: add new (deduped) tracks to end of existing playlist.
            (
                result_playlist,
                operations_performed,
                operation_counts,
            ) = await self._apply_append(
                current_playlist, processed_playlist, uow, command.dry_run
            )
            confidence_score = 1.0  # High confidence for simple append
        else:
            # Overwrite mode: diff resolved tracks for the operation counts.
            diff = calculate_playlist_diff(current_playlist, processed_playlist)

            # Complete no-op test: same positions (resolved AND unresolved) in
            # the same order. A resolved-only `diff.has_changes` would miss an
            # unresolved-only change or a resolved/unresolved reorder.
            if current_playlist.membership_keys == processed_playlist.membership_keys:
                logger.info("No changes detected, playlist already up to date")
                return UpdateCanonicalPlaylistResult(
                    playlist=current_playlist,
                    execution_time_ms=timer.stop(),
                    confidence_score=diff.confidence_score,
                )

            # Execute differential operations
            result_playlist = current_playlist
            operations_performed = 0
            operation_counts = OperationCounts()

            if not command.dry_run:
                (
                    result_playlist,
                    operations_performed,
                    operation_counts,
                ) = await self._execute_operations(
                    current_playlist, diff, processed_playlist, uow
                )
            confidence_score = diff.confidence_score
            playlist_changes = build_playlist_changes(diff, command.playlist_id)

        # Extract metrics from new tracks and commit (only for non-dry runs;
        # the caller owns the boundary when commit=False).
        if not command.dry_run:
            await self.metrics_service.extract_track_metrics(
                processed_playlist.tracks, uow
            )
            if commit:
                await uow.commit()

        result = UpdateCanonicalPlaylistResult(
            playlist=result_playlist,
            operations_performed=operations_performed,
            operation_counts=operation_counts,
            execution_time_ms=timer.stop(),
            confidence_score=confidence_score,
            playlist_changes=playlist_changes,
        )

        logger.info(
            "Canonical playlist update completed",
            playlist_id=command.playlist_id,
            operations_performed=operations_performed,
            execution_time_ms=timer.elapsed_ms,
            dry_run=command.dry_run,
        )

        return result

    async def _resolve_target(
        self,
        command: UpdateCanonicalPlaylistCommand,
        current_playlist: Playlist,
        uow: UnitOfWorkProtocol,
    ) -> Playlist:
        """Resolve the command's source into the target Playlist to reconcile against.

        A ConnectorPlaylist is ingested by the processing service (which builds
        the complete ordered entries — resolved AND unresolved). A bare TrackList
        is wrapped under the existing name with a fresh ``added_at`` for its new
        entries.
        """
        source_data = command.new_tracklist
        if command.connector_playlist is not None:
            from src.application.services.connector_playlist_processing_service import (
                ConnectorPlaylistProcessingService,
            )

            processing_service = ConnectorPlaylistProcessingService()
            source_data = await processing_service.process_connector_playlist(
                command.connector_playlist, uow, user_id=command.user_id
            )

        if isinstance(source_data, Playlist):
            return source_data
        return Playlist.from_tracklist(
            name=current_playlist.name,  # Use existing name
            tracklist=source_data,
            added_at=datetime.now(UTC),  # Timestamp for new tracks
        )

    async def _execute_operations(
        self,
        current_playlist: Playlist,
        diff: PlaylistDiff,
        target_playlist: Playlist,
        uow: UnitOfWorkProtocol,
    ) -> tuple[Playlist, int, OperationCounts]:
        """Persists the target membership, preserving identity for unchanged rows.

        Hands the repository the COMPLETE desired entry list — resolved AND
        unresolved positions, in source order — and lets its membership matching
        compute the minimal row delta (preserving record id + added_at for tracks
        already present). ``diff`` supplies only the operation counts; the
        repository, not a resolved-only reorder, owns what actually changes.

        Args:
            current_playlist: Playlist before changes
            diff: Calculated operations (used for counts/metrics)
            target_playlist: Target playlist state (with entries)
            uow: Database transaction manager

        Returns:
            Tuple of (updated_playlist, operations_performed, operation_counts)
        """
        logger.debug(f"Executing {len(diff.operations)} operations")
        operation_counts = count_operation_types(diff.operations)

        updated_entries = current_playlist.reconcile_entries_from(target_playlist)
        updated_playlist = current_playlist.with_entries(updated_entries).with_metadata({
            **current_playlist.metadata,
            "last_updated": datetime.now(UTC).isoformat(),
        })

        playlist_repo = uow.get_playlist_repository()
        saved_playlist = await playlist_repo.save_playlist(updated_playlist)

        return (saved_playlist, len(diff.operations), operation_counts)

    async def _update_playlist_metadata(
        self,
        current_playlist: Playlist,
        command: UpdateCanonicalPlaylistCommand,
        uow: UnitOfWorkProtocol,
    ) -> Playlist:
        """Updates playlist name and/or description if provided in command.

        Args:
            current_playlist: Playlist to update
            command: Contains optional new name and description
            uow: Database transaction manager

        Returns:
            Playlist with updated metadata, or unchanged playlist if no updates
        """
        updates: dict[str, str] = {}
        if command.playlist_name is not None:
            updates["name"] = command.playlist_name
        if command.playlist_description is not None:
            updates["description"] = command.playlist_description

        if updates:
            logger.info(
                "Updating playlist metadata",
                playlist_id=current_playlist.id,
                updates=updates,
            )
            # Preserve connector mappings and entries when updating metadata
            updated_playlist = evolve(
                current_playlist,
                name=updates.get("name", current_playlist.name),
                description=updates.get("description", current_playlist.description),
            )

            # Persist metadata changes using update_playlist for existing playlists
            playlist_repo = uow.get_playlist_repository()
            return await playlist_repo.save_playlist(updated_playlist)

        return current_playlist

    async def _apply_append(
        self,
        current_playlist: Playlist,
        new_playlist: Playlist,
        uow: UnitOfWorkProtocol,
        dry_run: bool,
    ) -> tuple[Playlist, int, OperationCounts]:
        """Persists the deduped appended entries at the end of the playlist.

        The dedup-vs-append decision is the pure ``select_appendable_entries``
        domain function (tracks already present are dropped; unresolved positions
        are kept). This method owns only the persistence: build the appended
        playlist, stamp ``last_updated``, and save — unless ``dry_run``.

        Returns:
            Tuple of (updated_playlist, operations_performed, operation_counts)
        """
        new_entries = select_appendable_entries(
            current_playlist.entries, new_playlist.entries
        )
        if not new_entries:
            logger.info("No new entries to append")
            return current_playlist, 0, OperationCounts()

        logger.info(f"Appending {len(new_entries)} new entries to playlist")
        counts = OperationCounts(added=len(new_entries))
        appended = current_playlist.with_entries(current_playlist.entries + new_entries)

        if dry_run:
            return appended, len(new_entries), counts

        saved_playlist = await uow.get_playlist_repository().save_playlist(
            appended.with_metadata({
                **current_playlist.metadata,
                "last_updated": datetime.now(UTC).isoformat(),
                "entries_appended": len(new_entries),
            })
        )
        return saved_playlist, len(new_entries), counts
