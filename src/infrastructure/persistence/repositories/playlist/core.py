"""Database operations for playlists, tracks, and external service mappings.

Handles CRUD operations for playlists from multiple music services (Spotify, Last.fm,
MusicBrainz), maintaining track ordering and synchronizing external IDs.
"""

from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import Select, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import get_logger
from src.domain.entities import Playlist, Track
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

    # Extended relationship mapping for automatic loading
    _RELATIONSHIP_PATHS: ClassVar[dict[str, list[str]]] = {
        "full": [
            "mappings",
            "tracks.track.mappings.connector_track",
        ],
    }

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

    def select_with_relations(self) -> Select:
        """Build query that loads playlist with tracks and external mappings."""
        return self.with_playlist_relationships(self.select())

    def select_by_connector(self, connector: str, connector_id: str) -> Select:
        """Build query to find playlist by external service ID.

        Args:
            connector: Service name (spotify, lastfm, musicbrainz).
            connector_id: External playlist ID from that service.

        Returns:
            SQLAlchemy select statement.
        """
        return (
            self.select()
            .join(DBPlaylistMapping)
            .where(
                DBPlaylistMapping.connector_name == connector,
                DBPlaylistMapping.connector_playlist_id == connector_id,
                DBPlaylistMapping.is_deleted == False,  # noqa: E712
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

        updated_tracks = []
        for track in tracks:
            if not track.id:
                try:
                    if connector and connector in track.connector_track_ids:
                        # Use connector-first approach for tracks with connector data
                        connector_id = track.connector_track_ids[connector]
                        metadata = (
                            track.connector_metadata.get(connector, {})
                            if hasattr(track, "connector_metadata")
                            else {}
                        )

                        # Use the new ingest method that handles all aspects of track creation
                        saved_track = (
                            await self.connector_repository.ingest_external_track(
                                connector=connector,
                                connector_id=connector_id,
                                metadata=metadata,
                                title=track.title,
                                artists=[a.name for a in track.artists]
                                if track.artists
                                else [],
                                album=track.album,
                                duration_ms=track.duration_ms,
                                release_date=track.release_date,
                                isrc=track.isrc,
                            )
                        )
                        updated_tracks.append(saved_track)
                    else:
                        # For tracks without connector data, just save directly
                        saved_track = await self.track_repository.save_track(track)
                        updated_tracks.append(saved_track)
                except Exception as e:
                    # Use proper exception chaining
                    raise ValueError(f"Failed to save track: {e}") from e
            else:
                updated_tracks.append(track)

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
                            added_at = datetime.fromisoformat(
                                metadata["added_at"].replace("Z", "+00:00")
                            )
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
            # Get existing playlist tracks
            stmt = select(DBPlaylistTrack).where(
                DBPlaylistTrack.playlist_id == playlist_id,
                DBPlaylistTrack.is_deleted == False,  # noqa: E712
            )
            result = await self.session.scalars(stmt)
            existing_tracks = {pt.track_id: pt for pt in result.all()}

            # Track current IDs, updates and new additions
            current_track_ids = set()
            updates = []
            new_tracks = []

            # Process each track in the list
            for idx, track in enumerate(tracks):
                if not track.id:
                    continue

                current_track_ids.add(track.id)
                sort_key = self._generate_sort_key(idx)

                if track.id in existing_tracks:
                    # Update existing track's position if needed
                    pt = existing_tracks[track.id]
                    current_sort_key = getattr(pt, "sort_key", None)
                    if current_sort_key != sort_key:
                        updates.append((pt.id, sort_key))
                else:
                    # Add new track to playlist with added_at from connector metadata if available
                    added_at = None
                    for metadata in track.connector_metadata.values():
                        if metadata.get("added_at"):
                            try:
                                added_at = datetime.fromisoformat(
                                    metadata["added_at"].replace("Z", "+00:00")
                                )
                                break
                            except (ValueError, TypeError):
                                pass

                    new_tracks.append({
                        "playlist_id": playlist_id,
                        "track_id": track.id,
                        "sort_key": sort_key,
                        "added_at": added_at,
                        "created_at": now,
                        "updated_at": now,
                    })

            # Execute updates in batch
            if updates:
                for pt_id, sort_key in updates:
                    await self.session.execute(
                        update(DBPlaylistTrack)
                        .where(DBPlaylistTrack.id == pt_id)
                        .values(sort_key=sort_key, updated_at=now),
                    )

            # Handle new tracks in batch
            if new_tracks:
                await self.session.execute(insert(DBPlaylistTrack).values(new_tracks))

            # Soft delete tracks no longer in the playlist
            tracks_to_remove = set(existing_tracks.keys()) - current_track_ids
            if tracks_to_remove:
                await self.session.execute(
                    update(DBPlaylistTrack)
                    .where(
                        DBPlaylistTrack.playlist_id == playlist_id,
                        DBPlaylistTrack.track_id.in_(tracks_to_remove),
                        DBPlaylistTrack.is_deleted == False,  # noqa: E712
                    )
                    .values(is_deleted=True, deleted_at=now),
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

        now = datetime.now(UTC)

        if operation == "create":
            # Bulk create all mappings
            values = [
                {
                    "playlist_id": playlist_id,
                    "connector_name": connector,
                    "connector_playlist_id": external_id,
                    "created_at": now,
                    "updated_at": now,
                }
                for connector, external_id in connector_ids.items()
            ]

            if values:
                await self.session.execute(insert(DBPlaylistMapping).values(values))
                await self.session.flush()

        elif operation == "update":
            # Get existing mappings
            stmt = select(DBPlaylistMapping).where(
                DBPlaylistMapping.playlist_id == playlist_id,
                DBPlaylistMapping.is_deleted == False,  # noqa: E712
            )
            result = await self.session.scalars(stmt)
            existing = {m.connector_name: m for m in result.all()}

            # Track updates and new additions
            new_mappings = []
            update_mappings = []

            # Process each mapping
            for connector, connector_id in connector_ids.items():
                if connector in existing:
                    # Update if connector ID changed
                    mapping = existing[connector]
                    if mapping.connector_playlist_id != connector_id:
                        mapping.connector_playlist_id = connector_id
                        mapping.updated_at = now
                        update_mappings.append(mapping)
                else:
                    # Add new mapping
                    new_mappings.append({
                        "playlist_id": playlist_id,
                        "connector_name": connector,
                        "connector_playlist_id": connector_id,
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
            playlist.connector_playlist_ids
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
            playlist.connector_playlist_ids,
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
        # Update basic properties using base repository's update method
        updates = {
            "name": playlist.name,
            "description": playlist.description,
            "track_count": len(playlist.tracks) if playlist.tracks else 0,
            "updated_at": datetime.now(UTC),
        }

        # Update core properties
        await self.session.execute(
            update(self.model_class)
            .where(
                self.model_class.id == playlist_id,
                self.model_class.is_deleted == False,  # noqa: E712
            )
            .values(**updates),
        )

        # Determine source connector if available
        source_connector = self._determine_source_connector(
            playlist.connector_playlist_ids
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
            playlist.connector_playlist_ids,
            operation="update",
        )

        # Return the updated playlist with all relationships
        return await self.get_playlist_by_id(playlist_id)

    async def get_or_create(
        self,
        lookup_attrs: dict[str, Any],
        create_attrs: dict[str, Any] | None = None,
    ) -> tuple[Playlist, bool]:
        """Find existing playlist or create new one with given attributes.

        Degenerate case of get_or_create_many for single playlist.

        Args:
            lookup_attrs: Search criteria for existing playlist.
            create_attrs: Additional attributes for playlist creation.

        Returns:
            Tuple of (playlist, created_flag) where created_flag is True
            if playlist was newly created.

        Raises:
            ValueError: If playlist creation requires missing name.
        """
        results = await self.get_or_create_many([
            {"lookup_attrs": lookup_attrs, "create_attrs": create_attrs or {}}
        ])
        return results[0]

    @db_operation("get_or_create_many")
    async def get_or_create_many(
        self,
        playlist_specs: list[dict[str, Any]],
    ) -> list[tuple[Playlist, bool]]:
        """Batch find-or-create operation using proper batch queries.

        Uses batch lookups by name followed by individual creates for new playlists.
        Avoids invalid upsert usage since DBPlaylist has no unique constraints on name.

        Args:
            playlist_specs: List of dicts with 'lookup_attrs' and optional 'create_attrs'

        Returns:
            List of (playlist, created_flag) tuples in same order as input

        Raises:
            ValueError: If any playlist spec is invalid
        """
        if not playlist_specs:
            return []

        return await self.execute_transaction(
            lambda: self._get_or_create_many_impl(playlist_specs)
        )

    async def _get_or_create_many_impl(
        self,
        playlist_specs: list[dict[str, Any]],
    ) -> list[tuple[Playlist, bool]]:
        """Execute batch get-or-create using proper batch queries (no invalid upsert)."""
        # Validate and prepare specs
        validated_specs = []
        names_to_find = []

        for spec in playlist_specs:
            lookup_attrs = spec["lookup_attrs"]
            create_attrs = spec.get("create_attrs", {})

            # Merge all attributes
            all_attrs = {**lookup_attrs, **create_attrs}

            # Validate required name
            if "name" not in all_attrs or not all_attrs["name"]:
                raise ValueError("Playlist requires a name")

            validated_specs.append((all_attrs, spec))
            names_to_find.append(all_attrs["name"])

        # Phase 1: Batch lookup existing playlists by name
        existing_playlists = {}
        if names_to_find:
            # Use find_by to get playlists with any of these names
            found_playlists = (
                await self.find_by({"name": names_to_find[0]})
                if len(names_to_find) == 1
                else []
            )

            # For multiple names, we need to query each (since find_by doesn't support IN queries)
            if len(names_to_find) > 1:
                found_playlists = []
                for name in names_to_find:
                    name_results = await self.find_by({"name": name})
                    found_playlists.extend(name_results)

            # Map by name for quick lookup (take first if multiple with same name)
            for playlist in found_playlists:
                if playlist.name not in existing_playlists:
                    existing_playlists[playlist.name] = playlist

        # Phase 2: Separate existing vs new playlists
        results = []
        playlists_to_create = []

        for all_attrs, original_spec in validated_specs:
            playlist_name = all_attrs["name"]

            if playlist_name in existing_playlists:
                # Found existing playlist
                existing_playlist = existing_playlists[playlist_name]

                # Handle complex playlist updates with tracks/mappings if needed
                if original_spec.get("create_attrs", {}).get(
                    "tracks"
                ) or original_spec.get("create_attrs", {}).get(
                    "connector_playlist_ids"
                ):
                    # Update existing playlist with new content
                    full_playlist = Playlist(
                        id=existing_playlist.id,
                        name=existing_playlist.name,
                        description=all_attrs.get(
                            "description", existing_playlist.description
                        ),
                        tracks=original_spec.get("create_attrs", {}).get("tracks", []),
                        connector_playlist_ids=original_spec.get(
                            "create_attrs", {}
                        ).get("connector_playlist_ids", {}),
                    )

                    if full_playlist.tracks or full_playlist.connector_playlist_ids:
                        if existing_playlist.id is None:
                            raise ValueError("Existing playlist ID is None")
                        updated_playlist = await self.update_playlist(
                            existing_playlist.id, full_playlist
                        )
                        results.append((updated_playlist, False))  # Found and updated
                    else:
                        results.append((
                            existing_playlist,
                            False,
                        ))  # Found, no updates needed
                else:
                    results.append((existing_playlist, False))  # Found existing
            else:
                # Need to create new playlist
                playlists_to_create.append((all_attrs, original_spec))

        # Phase 3: Bulk create new playlists
        if playlists_to_create:
            for all_attrs, original_spec in playlists_to_create:
                # Create playlist domain object
                new_playlist = Playlist(
                    name=all_attrs["name"],
                    description=all_attrs.get("description"),
                    tracks=original_spec.get("create_attrs", {}).get("tracks", []),
                    connector_playlist_ids=original_spec.get("create_attrs", {}).get(
                        "connector_playlist_ids", {}
                    ),
                )

                # Use appropriate creation method based on complexity
                if new_playlist.tracks or new_playlist.connector_playlist_ids:
                    # Complex playlist with tracks/mappings - use full save_playlist
                    created_playlist = await self.save_playlist(new_playlist)
                else:
                    # Simple playlist - use basic create
                    created_playlist = await self.create(new_playlist)

                results.append((created_playlist, True))  # Created new

        return results

    @db_operation("playlist delete")
    async def delete_playlist(self, playlist_id: int) -> bool:
        """Soft delete playlist and all related tracks/mappings.

        Args:
            playlist_id: Internal playlist ID to delete.

        Returns:
            True if playlist was deleted, False if it didn't exist.
        """
        logger.info("Deleting playlist", playlist_id=playlist_id)

        # Soft delete the playlist
        now = datetime.now(UTC)
        result = await self.session.execute(
            update(DBPlaylist)
            .where(
                DBPlaylist.id == playlist_id,
                DBPlaylist.is_deleted == False,  # noqa: E712
            )
            .values(is_deleted=True, deleted_at=now)
        )

        playlist_deleted = result.rowcount > 0

        if playlist_deleted:
            # Also soft delete all playlist tracks and mappings
            await self._soft_delete_playlist_relations(playlist_id, now)
            logger.info("Playlist deleted successfully", playlist_id=playlist_id)
        else:
            logger.warning("Playlist not found for deletion", playlist_id=playlist_id)

        return playlist_deleted

    async def _soft_delete_playlist_relations(
        self, playlist_id: int, deletion_time: datetime
    ) -> None:
        """Mark playlist tracks and external mappings as deleted.

        Args:
            playlist_id: Playlist ID whose relations to delete.
            deletion_time: Timestamp for deletion.
        """
        # Soft delete playlist tracks
        await self.session.execute(
            update(DBPlaylistTrack)
            .where(
                DBPlaylistTrack.playlist_id == playlist_id,
                DBPlaylistTrack.is_deleted == False,  # noqa: E712
            )
            .values(is_deleted=True, deleted_at=deletion_time)
        )

        # Soft delete playlist mappings (connector connections)
        await self.session.execute(
            update(DBPlaylistMapping)
            .where(
                DBPlaylistMapping.playlist_id == playlist_id,
                DBPlaylistMapping.is_deleted == False,  # noqa: E712
            )
            .values(is_deleted=True, deleted_at=deletion_time)
        )
