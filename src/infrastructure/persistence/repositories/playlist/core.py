"""Database operations for playlists, tracks, and external service mappings.

Handles CRUD operations for playlists from multiple music services (Spotify, Last.fm,
MusicBrainz), maintaining track ordering and synchronizing external IDs.
"""

from datetime import UTC, datetime

from sqlalchemy import Select, delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import get_logger
from src.domain.entities import ConnectorTrack, Playlist, Track
from src.infrastructure.persistence.database.db_models import (
    DBPlaylist,
    DBPlaylistMapping,
    DBPlaylistTrack,
    DBTrack,
    DBTrackMapping,
)
from src.infrastructure.persistence.repositories.base_repo import BaseRepository
from src.infrastructure.persistence.repositories.playlist.mapper import PlaylistMapper
from src.infrastructure.persistence.repositories.repo_decorator import db_operation
from src.infrastructure.persistence.repositories.track.connector import (
    TrackConnectorRepository,
)
from src.infrastructure.persistence.repositories.track.core import (
    TrackRepository as CoreTrackRepository,
)

# Create module logger
logger = get_logger(__name__)


class PlaylistRepository(BaseRepository[DBPlaylist, Playlist]):
    """Manages playlist storage with tracks and external service mappings.

    Supports bulk track operations, maintains sort order, and syncs with external
    music services like Spotify and Last.fm.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize with database session and dependent repositories.

        Args:
            session: SQLAlchemy async session for database operations.
        """
        super().__init__(
            session=session,
            model_class=DBPlaylist,
            mapper=PlaylistMapper(),
        )
        # Initialize track repositories for managing playlist contents
        self.track_repository = CoreTrackRepository(session)
        self.connector_repository = TrackConnectorRepository(session)

    # -------------------------------------------------------------------------
    # ENHANCED QUERY METHODS
    # -------------------------------------------------------------------------

    def select_by_connector(self, connector: str, connector_id: str) -> Select:
        """Build query to find playlist by external service ID.

        Args:
            connector: Service name (spotify, lastfm, musicbrainz).
            connector_id: External playlist ID from that service.

        Returns:
            SQLAlchemy select statement.
        """
        from src.infrastructure.persistence.database.db_models import (
            DBConnectorPlaylist,
        )

        return (
            self.select()
            .join(DBPlaylistMapping)
            .join(
                DBConnectorPlaylist,
                DBPlaylistMapping.connector_playlist_id == DBConnectorPlaylist.id,
            )
            .where(
                DBPlaylistMapping.connector_name == connector,
                DBConnectorPlaylist.connector_playlist_identifier == connector_id,
            )
        )

    def with_playlist_relationships(
        self,
        stmt: Select[tuple[DBPlaylist]],
    ) -> Select[tuple[DBPlaylist]]:
        """Add eager loading for playlist tracks and external service mappings.

        Args:
            stmt: Base SQLAlchemy select statement.

        Returns:
            Enhanced statement with relationship loading.
        """
        return stmt.options(
            selectinload(self.model_class.mappings),
            selectinload(self.model_class.tracks)
            .selectinload(DBPlaylistTrack.track)
            .selectinload(DBTrack.mappings)
            .selectinload(DBTrackMapping.connector_track),
        )

    # -------------------------------------------------------------------------
    # HELPER METHODS (non-decorated)
    # -------------------------------------------------------------------------

    async def _save_new_tracks(
        self,
        tracks: list[Track],
        connector: str | None = None,
    ) -> list[Track]:
        """Persist tracks without IDs and return updated tracks with IDs.

        Uses connector-specific ingestion for tracks with external metadata,
        falls back to direct save for local tracks.

        Args:
            tracks: List of track entities to save.
            connector: Preferred external service for metadata lookup.

        Returns:
            List of tracks with assigned database IDs.

        Raises:
            ValueError: If track save operation fails.
        """
        if not tracks:
            return []

        # Separate tracks by processing type for batch operations
        connector_tracks_to_save = []
        direct_tracks_to_save = []
        track_positions = {}  # Track original positions for result ordering

        for idx, track in enumerate(tracks):
            if track.id:
                # Track already has ID, no processing needed
                track_positions[idx] = ("existing", track)
            elif connector and connector in track.connector_track_identifiers:
                # Track needs connector ingestion
                connector_track = ConnectorTrack(
                    connector_name=connector,
                    connector_track_identifier=track.connector_track_identifiers[
                        connector
                    ],
                    title=track.title,
                    artists=track.artists,
                    album=track.album,
                    duration_ms=track.duration_ms,
                    release_date=track.release_date,
                    isrc=track.isrc,
                    raw_metadata=(
                        track.connector_metadata.get(connector, {})
                        if hasattr(track, "connector_metadata")
                        else {}
                    ),
                )
                connector_tracks_to_save.append(connector_track)
                track_positions[idx] = ("connector", len(connector_tracks_to_save) - 1)
            else:
                # Track needs direct saving
                direct_tracks_to_save.append(track)
                track_positions[idx] = ("direct", len(direct_tracks_to_save) - 1)

        # Batch process connector tracks
        saved_connector_tracks = []
        if connector_tracks_to_save and connector:
            try:
                saved_connector_tracks = (
                    await self.connector_repository.ingest_external_tracks_bulk(
                        connector, connector_tracks_to_save
                    )
                )
            except Exception as e:
                raise ValueError(f"Failed to save connector tracks: {e}") from e

        # Process direct tracks individually (they may have different external IDs)
        saved_direct_tracks = []
        for track in direct_tracks_to_save:
            try:
                saved_track = await self.track_repository.save_track(track)
                saved_direct_tracks.append(saved_track)
            except Exception as e:
                raise ValueError(f"Failed to save track: {e}") from e

        # Reconstruct results in original order
        updated_tracks = []
        for idx in range(len(tracks)):
            track_type, position = track_positions[idx]
            if track_type == "existing":
                updated_tracks.append(position)  # position is the track itself
            elif track_type == "connector":
                if position < len(saved_connector_tracks):
                    updated_tracks.append(saved_connector_tracks[position])
                else:
                    raise ValueError(
                        f"Connector track at position {position} failed to save"
                    )
            elif track_type == "direct":
                if position < len(saved_direct_tracks):
                    updated_tracks.append(saved_direct_tracks[position])
                else:
                    raise ValueError(
                        f"Direct track at position {position} failed to save"
                    )

        return updated_tracks

    async def _manage_playlist_tracks(
        self,
        playlist_id: int,
        tracks: list[Track],
        operation: str = "create",
    ) -> None:
        """Manage playlist-track associations with bulk database operations.

        Handles initial track assignment and subsequent reordering/updates.
        Preserves external 'added_at' timestamps from service metadata.

        Args:
            playlist_id: Target playlist database ID.
            tracks: Ordered list of tracks for the playlist.
            operation: Either 'create' for new playlist or 'update' for existing.
        """
        if not tracks:
            return

        now = datetime.now(UTC)

        if operation == "create":
            # Bulk insert tracks with sort keys and added_at timestamps from connector data if available
            values = []
            for idx, track in enumerate(tracks):
                if track.id is None:
                    continue

                # Get added_at timestamp from connector metadata if available
                added_at = None
                for metadata in track.connector_metadata.values():
                    if metadata.get("added_at"):
                        try:
                            added_at = datetime.fromisoformat(metadata["added_at"])
                            break
                        except (ValueError, TypeError):
                            pass

                values.append({
                    "playlist_id": playlist_id,
                    "track_id": track.id,
                    "sort_key": self._generate_sort_key(idx),
                    "added_at": added_at,
                    "created_at": now,
                    "updated_at": now,
                })

            if values:
                await self.session.execute(insert(DBPlaylistTrack).values(values))
                await self.session.flush()

        elif operation == "update":
            # Respect the precise track ordering from the use case layer
            # The use case layer has calculated exact diffs - we just need to update the ordering

            # Get existing tracks sorted by sort_key (current position order)
            stmt = (
                select(DBPlaylistTrack)
                .where(
                    DBPlaylistTrack.playlist_id == playlist_id,
                )
                .order_by(DBPlaylistTrack.sort_key)
            )
            result = await self.session.scalars(stmt)
            existing_tracks = list(result.all())

            # Step 1: Identify tracks to remove (exist in current but not in target)
            target_track_ids = {track.id for track in tracks if track.id is not None}
            existing_track_ids = {pt.track_id for pt in existing_tracks}

            tracks_to_remove_ids = existing_track_ids - target_track_ids

            # Step 2: Explicitly remove the identified tracks (hard delete)
            records_to_remove = [
                existing_record
                for existing_record in existing_tracks
                if existing_record.track_id in tracks_to_remove_ids
            ]

            # Step 3: Get remaining tracks (after removals) for position mapping
            remaining_tracks = [
                pt for pt in existing_tracks if pt.track_id not in tracks_to_remove_ids
            ]

            # Step 4: Map target tracks to existing position records (preserve metadata)
            values_to_insert = []
            records_to_update = []

            for idx, track in enumerate(tracks):
                if track.id is None:
                    continue

                sort_key = self._generate_sort_key(idx)

                if idx < len(remaining_tracks):
                    # Update existing record at this position with new track and sort_key
                    existing_record = remaining_tracks[idx]
                    existing_record.track_id = track.id
                    existing_record.sort_key = sort_key
                    existing_record.updated_at = now
                    records_to_update.append(existing_record)
                else:
                    # Insert new record for additional tracks
                    added_at = None
                    if hasattr(track, "connector_metadata"):
                        # Get added_at from connector metadata if available
                        for metadata in track.connector_metadata.values():
                            if metadata.get("added_at"):
                                try:
                                    added_at = datetime.fromisoformat(
                                        metadata["added_at"]
                                    )
                                    break
                                except (ValueError, TypeError):
                                    pass

                    values_to_insert.append({
                        "playlist_id": playlist_id,
                        "track_id": track.id,
                        "sort_key": sort_key,
                        "added_at": added_at,
                        "created_at": now,
                        "updated_at": now,
                    })

            # Commit all changes while preserving metadata

            if records_to_update:
                self.session.add_all(records_to_update)

            if records_to_remove:
                for record in records_to_remove:
                    await self.session.delete(record)

            if values_to_insert:
                await self.session.execute(
                    insert(DBPlaylistTrack).values(values_to_insert)
                )

            await self.session.flush()

    async def _manage_connector_mappings(
        self,
        playlist_id: int,
        connector_ids: dict[str, str],
        operation: str = "create",
    ) -> None:
        """Manage external service ID mappings for playlist synchronization.

        Links internal playlist ID with external service identifiers for syncing
        across Spotify, Last.fm, MusicBrainz, etc.

        Args:
            playlist_id: Internal database playlist ID.
            connector_ids: Map of service names to external playlist IDs.
            operation: Either 'create' for new mappings or 'update' for existing.
        """
        if not connector_ids:
            return

        from src.infrastructure.persistence.database.db_models import (
            DBConnectorPlaylist,
        )

        now = datetime.now(UTC)

        # First ensure connector playlists exist and get their database IDs
        connector_playlist_db_ids = {}
        for connector_name, external_id in connector_ids.items():
            # Check if connector playlist exists
            stmt = select(DBConnectorPlaylist).where(
                DBConnectorPlaylist.connector_name == connector_name,
                DBConnectorPlaylist.connector_playlist_identifier == external_id,
            )
            result = await self.session.execute(stmt)
            connector_playlist = result.scalar_one_or_none()

            if connector_playlist:
                connector_playlist_db_ids[connector_name] = connector_playlist.id
            else:
                # Create minimal connector playlist entry if it doesn't exist
                # This allows mappings to work even if the full playlist sync hasn't happened yet
                new_connector_playlist = DBConnectorPlaylist(
                    connector_name=connector_name,
                    connector_playlist_identifier=external_id,
                    name=f"Playlist {external_id}",  # Placeholder name
                    description=None,
                    owner=None,
                    owner_id=None,
                    is_public=False,
                    collaborative=False,
                    follower_count=None,
                    items=[],
                    raw_metadata={},
                    last_updated=now,
                    created_at=now,
                    updated_at=now,
                )
                self.session.add(new_connector_playlist)
                await self.session.flush()
                await self.session.refresh(new_connector_playlist)
                connector_playlist_db_ids[connector_name] = new_connector_playlist.id

        if operation == "create":
            # Bulk create all mappings using connector playlist database IDs
            values = [
                {
                    "playlist_id": playlist_id,
                    "connector_name": connector_name,
                    "connector_playlist_id": connector_playlist_db_ids[connector_name],
                    "created_at": now,
                    "updated_at": now,
                }
                for connector_name in connector_ids
                if connector_name in connector_playlist_db_ids
            ]

            if values:
                await self.session.execute(insert(DBPlaylistMapping).values(values))
                await self.session.flush()

        elif operation == "update":
            # Get existing mappings
            stmt = select(DBPlaylistMapping).where(
                DBPlaylistMapping.playlist_id == playlist_id,
            )
            result = await self.session.scalars(stmt)
            existing = {m.connector_name: m for m in result.all()}

            # Track updates and new additions
            new_mappings = []
            update_mappings = []

            # Process each mapping
            for connector_name in connector_ids:
                if connector_name not in connector_playlist_db_ids:
                    continue  # Skip if we couldn't resolve the connector playlist

                connector_playlist_db_id = connector_playlist_db_ids[connector_name]

                if connector_name in existing:
                    # Update if connector playlist ID changed
                    mapping = existing[connector_name]
                    if mapping.connector_playlist_id != connector_playlist_db_id:
                        mapping.connector_playlist_id = connector_playlist_db_id
                        mapping.updated_at = now
                        update_mappings.append(mapping)
                else:
                    # Add new mapping
                    new_mappings.append({
                        "playlist_id": playlist_id,
                        "connector_name": connector_name,
                        "connector_playlist_id": connector_playlist_db_id,
                        "created_at": now,
                        "updated_at": now,
                    })

            # Execute updates
            if update_mappings:
                self.session.add_all(update_mappings)

            # Execute inserts
            if new_mappings:
                await self.session.execute(
                    insert(DBPlaylistMapping).values(new_mappings)
                )

            await self.session.flush()

    # -------------------------------------------------------------------------
    # UTILITY METHODS
    # -------------------------------------------------------------------------

    @staticmethod
    def _generate_sort_key(position: int) -> str:
        """Generate lexicographic sort key for track ordering."""
        return f"a{position:08d}"

    @staticmethod
    def _determine_source_connector(connector_ids: dict[str, str]) -> str | None:
        """Find preferred external service for metadata operations.

        Args:
            connector_ids: Available external service mappings.

        Returns:
            Service name in priority order (spotify > lastfm > musicbrainz).
        """
        for connector in ["spotify", "lastfm", "musicbrainz"]:
            if connector in connector_ids:
                return connector
        return None

    # -------------------------------------------------------------------------
    # PUBLIC API METHODS (decorated)
    # -------------------------------------------------------------------------

    @db_operation("get_playlist_by_id")
    async def get_playlist_by_id(
        self,
        playlist_id: int,
    ) -> Playlist:
        """Retrieve playlist with all tracks and external service mappings.

        Args:
            playlist_id: Internal database ID.

        Returns:
            Complete playlist entity with tracks and mappings.

        Raises:
            ValueError: If playlist not found.
        """
        stmt = self.select_by_id(playlist_id)
        stmt = self.with_playlist_relationships(stmt)

        # Execute query using the base repository method
        db_model = await self.execute_select_one(stmt)

        if not db_model:
            raise ValueError(f"Playlist with ID {playlist_id} not found")

        # Convert to domain model
        return await self.mapper.to_domain(db_model)

    @db_operation("get_playlist_by_connector")
    async def get_playlist_by_connector(
        self,
        connector: str,
        connector_id: str,
        raise_if_not_found: bool = True,
    ) -> Playlist | None:
        """Find playlist by external service ID (Spotify, Last.fm, etc.).

        Args:
            connector: Service name (spotify, lastfm, musicbrainz).
            connector_id: External playlist identifier.
            raise_if_not_found: Whether to raise exception if not found.

        Returns:
            Playlist entity or None if not found and raise_if_not_found=False.

        Raises:
            ValueError: If playlist not found and raise_if_not_found=True.
        """
        # Use the enhanced select method
        stmt = self.select_by_connector(connector, connector_id)

        # Add eager loading with our helper
        stmt = self.with_playlist_relationships(stmt)

        # Execute query using base repository method
        db_model = await self.execute_select_one(stmt)

        if not db_model:
            if raise_if_not_found:
                raise ValueError(f"Playlist for {connector}:{connector_id} not found")
            return None

        # Convert to domain model
        return await self.mapper.to_domain(db_model)

    @db_operation("save_playlist")
    async def save_playlist(
        self,
        playlist: Playlist,
    ) -> Playlist:
        """Create new playlist with all tracks and external mappings atomically.

        Args:
            playlist: Playlist entity with tracks and connector IDs.

        Returns:
            Saved playlist with database IDs assigned.

        Raises:
            ValueError: If playlist lacks required name.
        """
        if not playlist.name:
            raise ValueError("Playlist must have a name")

        # Execute in a transaction using base repository's helper
        return await self.execute_transaction(
            lambda: self._save_playlist_impl(playlist)
        )

    async def _save_playlist_impl(self, playlist: Playlist) -> Playlist:
        """Execute playlist creation with tracks and mappings."""
        # Determine source connector if available
        source_connector = self._determine_source_connector(
            playlist.connector_playlist_identifiers
        )

        # Save tracks first with source connector for proper mappings
        updated_tracks = await self._save_new_tracks(
            playlist.tracks,
            connector=source_connector,
        )

        # Create the playlist DB entity
        db_playlist = self.mapper.to_db(playlist)
        self.session.add(db_playlist)
        await self.session.flush()
        await self.session.refresh(db_playlist)

        # Ensure we got an ID
        if db_playlist.id is None:
            raise ValueError("Failed to create playlist: no ID was generated")

        # Add mappings and tracks with batch operations
        await self._manage_connector_mappings(
            db_playlist.id,
            playlist.connector_playlist_identifiers,
            operation="create",
        )
        await self._manage_playlist_tracks(
            db_playlist.id,
            updated_tracks,
            operation="create",
        )

        # Return a fresh copy with all relationships eager-loaded
        return await self.get_playlist_by_id(db_playlist.id)

    @db_operation("update_playlist")
    async def update_playlist(
        self,
        playlist_id: int,
        playlist: Playlist,
    ) -> Playlist:
        """Update existing playlist metadata, tracks, and external mappings.

        Args:
            playlist_id: Database ID of playlist to update.
            playlist: Updated playlist data.

        Returns:
            Updated playlist entity.

        Raises:
            ValueError: If playlist lacks required name.
        """
        if not playlist.name:
            raise ValueError("Playlist must have a name")

        # Execute in a transaction using base repository method
        return await self.execute_transaction(
            lambda: self._update_playlist_impl(playlist_id, playlist)
        )

    async def _update_playlist_impl(
        self,
        playlist_id: int,
        playlist: Playlist,
    ) -> Playlist:
        """Execute playlist update with track reordering and mapping sync."""
        # Count actual tracks that will be inserted (not input count)
        actual_track_count = len([
            track for track in playlist.tracks if track.id is not None
        ])

        # Update basic properties using base repository's update method
        updates = {
            "name": playlist.name,
            "description": playlist.description,
            "track_count": actual_track_count,  # Use actual count, not input count
            "updated_at": datetime.now(UTC),
        }

        # Update core properties
        await self.session.execute(
            update(self.model_class)
            .where(
                self.model_class.id == playlist_id,
            )
            .values(**updates),
        )

        # Determine source connector if available
        source_connector = self._determine_source_connector(
            playlist.connector_playlist_identifiers
        )

        # Process tracks and mappings in parallel
        if playlist.tracks:
            updated_tracks = await self._save_new_tracks(
                playlist.tracks,
                connector=source_connector,
            )
            await self._manage_playlist_tracks(
                playlist_id,
                updated_tracks,
                operation="update",
            )

        # Update connector mappings
        await self._manage_connector_mappings(
            playlist_id,
            playlist.connector_playlist_identifiers,
            operation="update",
        )

        # Flush changes to ensure they're visible to subsequent queries
        await self.session.flush()

        # Return the updated playlist with all relationships
        return await self.get_playlist_by_id(playlist_id)

    @db_operation("playlist delete")
    async def delete_playlist(self, playlist_id: int) -> bool:
        """Soft delete playlist and all related tracks/mappings.

        Args:
            playlist_id: Internal playlist ID to delete.

        Returns:
            True if playlist was deleted, False if it didn't exist.
        """
        logger.info("Deleting playlist", playlist_id=playlist_id)

        # Hard delete the playlist (CASCADE will handle related records)
        result = await self.session.execute(
            delete(DBPlaylist)
            .where(DBPlaylist.id == playlist_id)
            .returning(DBPlaylist.id)
        )

        deleted_ids = result.scalars().all()
        playlist_deleted = len(deleted_ids) > 0

        if playlist_deleted:
            logger.info("Playlist deleted successfully", playlist_id=playlist_id)
        else:
            logger.warning("Playlist not found for deletion", playlist_id=playlist_id)

        return playlist_deleted

    # _soft_delete_playlist_relations method removed - CASCADE handles related record deletion
