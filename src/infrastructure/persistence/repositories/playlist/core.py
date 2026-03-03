"""Database operations for playlists, tracks, and external service mappings.

Handles CRUD operations for playlists from multiple music services (Spotify, Last.fm,
MusicBrainz), maintaining track ordering and synchronizing external IDs.
"""

# pyright: reportAny=false

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import Select, delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import get_logger
from src.domain.entities import ConnectorTrack, Playlist, PlaylistEntry, Track
from src.infrastructure.persistence.database.db_models import (
    DBConnectorPlaylist,
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

    track_repository: CoreTrackRepository
    connector_repository: TrackConnectorRepository

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

    def select_by_connector(
        self, connector: str, connector_id: str
    ) -> Select[tuple[DBPlaylist]]:
        """Build query to find playlist by external service ID.

        Args:
            connector: Service name (spotify, lastfm, musicbrainz).
            connector_id: External playlist ID from that service.

        Returns:
            SQLAlchemy select statement.
        """

        return (
            self
            .select()
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
        connector_tracks_to_save: list[ConnectorTrack] = []
        direct_tracks_to_save: list[Track] = []
        # Track original positions for result ordering: idx -> (type, index_or_track)
        existing_track_positions: dict[int, Track] = {}
        connector_track_positions: dict[int, int] = {}
        direct_track_positions: dict[int, int] = {}

        for idx, track in enumerate(tracks):
            if track.id:
                # Track already has ID, no processing needed
                existing_track_positions[idx] = track
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
                connector_track_positions[idx] = len(connector_tracks_to_save) - 1
            else:
                # Track needs direct saving
                direct_tracks_to_save.append(track)
                direct_track_positions[idx] = len(direct_tracks_to_save) - 1

        # Batch process connector tracks
        saved_connector_tracks: list[Track] = []
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
        saved_direct_tracks: list[Track] = []
        for track in direct_tracks_to_save:
            try:
                saved_track = await self.track_repository.save_track(track)
                saved_direct_tracks.append(saved_track)
            except Exception as e:
                raise ValueError(f"Failed to save track: {e}") from e

        # Reconstruct results in original order
        updated_tracks: list[Track] = []
        for idx in range(len(tracks)):
            if idx in existing_track_positions:
                updated_tracks.append(existing_track_positions[idx])
            elif idx in connector_track_positions:
                position = connector_track_positions[idx]
                if position < len(saved_connector_tracks):
                    updated_tracks.append(saved_connector_tracks[position])
                else:
                    raise ValueError(
                        f"Connector track at position {position} failed to save"
                    )
            elif idx in direct_track_positions:
                position = direct_track_positions[idx]
                if position < len(saved_direct_tracks):
                    updated_tracks.append(saved_direct_tracks[position])
                else:
                    raise ValueError(
                        f"Direct track at position {position} failed to save"
                    )

        return updated_tracks

    async def _create_playlist_tracks(
        self,
        playlist_id: int,
        entries: list[PlaylistEntry],
    ) -> None:
        """Create initial playlist-track associations for a new playlist.

        Uses bulk insert for optimal performance with new playlists.

        Args:
            playlist_id: Target playlist database ID.
            entries: Ordered list of playlist entries (track + position metadata).
        """
        if not entries:
            return

        now = datetime.now(UTC)

        # Bulk insert entries with sort keys and added_at timestamps from PlaylistEntry
        values: list[dict[str, object]] = []
        for idx, entry in enumerate(entries):
            if entry.track.id is None:
                continue

            # Direct access to added_at from PlaylistEntry - clean architecture!
            added_at = entry.added_at
            if added_at:
                logger.debug(
                    "Using added_at from PlaylistEntry for create operation",
                    track_id=entry.track.id,
                    added_at=added_at,
                )

            values.append({
                "playlist_id": playlist_id,
                "track_id": entry.track.id,
                "sort_key": self._generate_sort_key(idx),
                "added_at": added_at,
                "created_at": now,
                "updated_at": now,
            })

        if values:
            _ = await self.session.execute(insert(DBPlaylistTrack).values(values))
            await self.session.flush()

    async def _update_playlist_tracks(
        self,
        playlist_id: int,
        entries: list[PlaylistEntry],
    ) -> None:
        """Update existing playlist-track associations preserving metadata.

        Uses consumption-based matching to preserve track membership instances
        when tracks are reordered. Only updates sort_key while preserving
        added_at timestamps and record IDs.

        Args:
            playlist_id: Target playlist database ID.
            entries: Ordered list of playlist entries (track + position metadata).
        """
        if not entries:
            return

        now = datetime.now(UTC)

        # CRITICAL: DBPlaylistTrack records represent "track membership instances"
        # NOT "position slots". When tracks move, we update the SAME record's sort_key,
        # preserving its id, added_at, and other metadata.
        #
        # Algorithm: Consumption-based matching by track identity
        # 1. Build pool of available records grouped by track_id
        # 2. For each target position, consume one record for that track
        # 3. Reuse record (preserve id, added_at) by updating only sort_key
        # 4. Delete unconsumed records (removed tracks)
        # 5. Create new records only for genuinely new memberships

        # Get all existing playlist track records
        stmt = select(DBPlaylistTrack).where(DBPlaylistTrack.playlist_id == playlist_id)
        result = await self.session.scalars(stmt)
        existing_records = list(result.all())

        # Build consumption pool: track_id → list of available records
        # This allows handling duplicate tracks (same track_id, multiple records)
        available_records: defaultdict[int, list[DBPlaylistTrack]] = defaultdict(list)
        for record in existing_records:
            available_records[record.track_id].append(record)

        logger.debug(
            f"Playlist update: {len(existing_records)} existing records, "
            + f"{len(available_records)} unique tracks, "
            + f"{len(entries)} target entries"
        )

        records_to_update: list[DBPlaylistTrack] = []
        records_to_create: list[DBPlaylistTrack] = []

        # Consume records for each target position
        for idx, entry in enumerate(entries):
            if entry.track.id is None:
                logger.warning(f"Skipping entry without track ID at position {idx}")
                continue

            sort_key = self._generate_sort_key(idx)

            if available_records[entry.track.id]:
                # CONSUME one existing record for this track
                # This preserves the record's id and added_at metadata
                record = available_records[entry.track.id].pop(0)
                record.sort_key = sort_key  # Update position only
                record.updated_at = now
                records_to_update.append(record)
            else:
                # No existing record for this track - create new membership instance
                # Direct access to added_at from PlaylistEntry - clean architecture!
                added_at = entry.added_at

                records_to_create.append(
                    DBPlaylistTrack(
                        playlist_id=playlist_id,
                        track_id=entry.track.id,
                        sort_key=sort_key,
                        added_at=added_at,
                        created_at=now,
                        updated_at=now,
                    )
                )

        # Collect unconsumed records (tracks removed from playlist)
        records_to_delete: list[DBPlaylistTrack] = []
        for remaining_records in available_records.values():
            records_to_delete.extend(remaining_records)

        logger.debug(
            f"Playlist sync: {len(records_to_update)} reused, {len(records_to_create)} created, {len(records_to_delete)} deleted"
        )

        # Execute database operations
        if records_to_update:
            self.session.add_all(records_to_update)
            logger.debug(f"Updating {len(records_to_update)} existing records")

        if records_to_create:
            self.session.add_all(records_to_create)
            logger.debug(f"Creating {len(records_to_create)} new records")

        if records_to_delete:
            for record in records_to_delete:
                await self.session.delete(record)
            logger.debug(f"Deleting {len(records_to_delete)} removed records")

        await self.session.flush()

    async def _manage_playlist_tracks(
        self,
        playlist_id: int,
        entries: list[PlaylistEntry],
        is_update: bool,
    ) -> None:
        """Manage playlist-track associations (delegates to focused methods).

        Args:
            playlist_id: Target playlist database ID.
            entries: Ordered list of playlist entries (track + position metadata).
            is_update: True for existing playlists, False for new playlists.
        """
        if is_update:
            await self._update_playlist_tracks(playlist_id, entries)
        else:
            await self._create_playlist_tracks(playlist_id, entries)

    async def _ensure_connector_playlists(
        self, connector_ids: dict[str, str]
    ) -> dict[str, int]:
        """Ensure connector playlists exist and return their database IDs.

        Creates minimal connector playlist entries if they don't exist yet,
        allowing mappings to work before full playlist sync.

        Args:
            connector_ids: Map of service names to external playlist IDs.

        Returns:
            Map of connector names to their database IDs.
        """

        now = datetime.now(UTC)
        connector_playlist_db_ids: dict[str, int] = {}

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

        return connector_playlist_db_ids

    async def _create_connector_mappings(
        self, playlist_id: int, connector_ids: dict[str, str]
    ) -> None:
        """Create initial connector mappings for a new playlist.

        Uses bulk insert for optimal performance with new playlists.

        Args:
            playlist_id: Internal database playlist ID.
            connector_ids: Map of service names to external playlist IDs.
        """
        if not connector_ids:
            return

        from src.infrastructure.persistence.database.db_models import (
            DBPlaylistMapping,
        )

        now = datetime.now(UTC)

        # Ensure connector playlists exist and get their database IDs
        connector_playlist_db_ids = await self._ensure_connector_playlists(
            connector_ids
        )

        # Bulk create all mappings
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
            _ = await self.session.execute(insert(DBPlaylistMapping).values(values))
            await self.session.flush()

    async def _update_connector_mappings(
        self, playlist_id: int, connector_ids: dict[str, str]
    ) -> None:
        """Update connector mappings for an existing playlist.

        Checks existing mappings, updates changed ones, and creates new ones.

        Args:
            playlist_id: Internal database playlist ID.
            connector_ids: Map of service names to external playlist IDs.
        """
        if not connector_ids:
            return

        from src.infrastructure.persistence.database.db_models import (
            DBPlaylistMapping,
        )

        now = datetime.now(UTC)

        # Ensure connector playlists exist and get their database IDs
        connector_playlist_db_ids = await self._ensure_connector_playlists(
            connector_ids
        )

        # Get existing mappings
        stmt = select(DBPlaylistMapping).where(
            DBPlaylistMapping.playlist_id == playlist_id,
        )
        result = await self.session.scalars(stmt)
        existing = {m.connector_name: m for m in result.all()}

        # Track updates and new additions
        new_mappings: list[dict[str, object]] = []
        update_mappings: list[DBPlaylistMapping] = []

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
            _ = await self.session.execute(
                insert(DBPlaylistMapping).values(new_mappings)
            )

        await self.session.flush()

    async def _manage_connector_mappings(
        self,
        playlist_id: int,
        connector_ids: dict[str, str],
        is_update: bool,
    ) -> None:
        """Manage connector mappings (delegates to focused methods).

        Args:
            playlist_id: Internal database playlist ID.
            connector_ids: Map of service names to external playlist IDs.
            is_update: True if updating existing playlist, False if creating new.
        """
        if is_update:
            await self._update_connector_mappings(playlist_id, connector_ids)
        else:
            await self._create_connector_mappings(playlist_id, connector_ids)

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
            from src.domain.exceptions import NotFoundError

            raise NotFoundError(f"Playlist with ID {playlist_id} not found")

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
            NotFoundError: If playlist not found and raise_if_not_found=True.
        """
        # Use the enhanced select method
        stmt = self.select_by_connector(connector, connector_id)

        # Add eager loading with our helper
        stmt = self.with_playlist_relationships(stmt)

        # Execute query using base repository method
        db_model = await self.execute_select_one(stmt)

        if not db_model:
            if raise_if_not_found:
                from src.domain.exceptions import NotFoundError

                raise NotFoundError(f"Playlist for {connector}:{connector_id} not found")
            return None

        # Convert to domain model
        return await self.mapper.to_domain(db_model)

    @db_operation("save_playlist")
    async def save_playlist(
        self,
        playlist: Playlist,
    ) -> Playlist:
        """Create or update playlist with all tracks and external mappings atomically.

        Uses playlist.id to detect create vs update operation. This unified method
        eliminates duplication between create and update paths.

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
        """Execute playlist create/update with tracks and mappings (unified implementation).

        This method handles both creation and updates, detecting the operation
        based on playlist.id existence. All shared logic (track persistence,
        entry rebuilding, relationship management) is consolidated here.
        """
        # Detect create vs update based on playlist.id
        is_update = playlist.id is not None

        # Determine source connector if available
        source_connector = self._determine_source_connector(
            playlist.connector_playlist_identifiers
        )

        # Save tracks first with source connector for proper mappings
        # Extract tracks from entries for persistence
        tracks_to_save = [entry.track for entry in playlist.entries]
        updated_tracks = await self._save_new_tracks(
            tracks_to_save,
            connector=source_connector,
        )

        # Rebuild entries with persisted tracks (preserving added_at metadata)
        updated_entries = [
            PlaylistEntry(
                track=updated_tracks[idx],
                added_at=playlist.entries[idx].added_at,
                added_by=playlist.entries[idx].added_by,
            )
            for idx in range(len(playlist.entries))
        ]

        # Create or update the playlist DB entity
        playlist_id: int
        if is_update:
            # Update existing playlist
            if playlist.id is None:
                raise ValueError("Cannot update playlist without an ID")

            actual_track_count = len([
                entry.track for entry in updated_entries if entry.track.id is not None
            ])

            updates = {
                "name": playlist.name,
                "description": playlist.description,
                "track_count": actual_track_count,
                "updated_at": datetime.now(UTC),
            }

            _ = await self.session.execute(
                update(self.model_class)
                .where(self.model_class.id == playlist.id)
                .values(**updates)
            )
            playlist_id = playlist.id
        else:
            # Create new playlist
            db_playlist = self.mapper.to_db(playlist)
            self.session.add(db_playlist)
            await self.session.flush()
            await self.session.refresh(db_playlist)

            playlist_id = db_playlist.id

        # Manage playlist tracks (unified for both create and update)
        await self._manage_playlist_tracks(
            playlist_id,
            updated_entries,
            is_update,
        )

        # Manage connector mappings (unified for both create and update)
        await self._manage_connector_mappings(
            playlist_id,
            playlist.connector_playlist_identifiers,
            is_update,
        )

        # Flush changes to ensure they're visible to subsequent queries
        await self.session.flush()

        # Return a fresh copy with all relationships eager-loaded
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
        playlist_deleted = bool(deleted_ids)

        if playlist_deleted:
            logger.info("Playlist deleted successfully", playlist_id=playlist_id)
        else:
            logger.warning("Playlist not found for deletion", playlist_id=playlist_id)

        return playlist_deleted

    @db_operation("playlist list all")
    async def list_all_playlists(self) -> list[Playlist]:
        """Get all playlists with basic metadata for efficient listing.

        Lightweight query that skips track loading entirely. Eagerly loads
        only mappings → connector_playlist (3 total queries) and builds
        Playlist objects directly from DB scalar columns.

        Returns:
            List of all stored playlists with basic metadata
        """
        logger.debug("Listing all playlists")

        # Eagerly load mappings chain for connector identifiers — no track loading
        stmt = (
            self
            .select()
            .options(
                selectinload(DBPlaylist.mappings)
                .selectinload(DBPlaylistMapping.connector_playlist),
            )
            .order_by(DBPlaylist.updated_at.desc())
        )

        result = await self.session.execute(stmt)
        db_playlists = result.scalars().all()

        # Build lightweight Playlist objects directly — no mapper.to_domain()
        playlists: list[Playlist] = []
        for db_playlist in db_playlists:
            # Extract connector identifiers from eagerly-loaded chain
            connector_ids: dict[str, str] = {}
            for mapping in db_playlist.mappings:
                cp = mapping.connector_playlist
                if cp is not None:
                    connector_ids[mapping.connector_name] = (
                        cp.connector_playlist_identifier
                    )

            playlists.append(
                Playlist(
                    id=db_playlist.id,
                    name=db_playlist.name,
                    description=db_playlist.description,
                    connector_playlist_identifiers=connector_ids,
                    updated_at=db_playlist.updated_at,
                    track_count=db_playlist.track_count,
                )
            )

        logger.info("Retrieved playlists for listing", count=len(playlists))
        return playlists

    @db_operation("update_playlist")
    async def update_playlist(self, playlist_id: int, playlist: Playlist) -> Playlist:
        """Update an existing playlist by ID.

        Args:
            playlist_id: Internal database ID of the playlist to update.
            playlist: Playlist entity with updated data.

        Returns:
            Updated playlist with all relationships loaded.
        """
        return await self.save_playlist(playlist.with_id(playlist_id))

    # _soft_delete_playlist_relations method removed - CASCADE handles related record deletion
