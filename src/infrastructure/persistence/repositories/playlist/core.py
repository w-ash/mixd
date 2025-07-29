"""Core playlist repository implementation."""

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
    """Repository for playlist operations with SQLAlchemy 2.0 best practices."""

    # Extended relationship mapping for automatic loading
    _RELATIONSHIP_PATHS: ClassVar[dict[str, list[str]]] = {
        "full": [
            "mappings",
            "tracks.track.mappings.connector_track",
        ],
    }

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with session and mapper."""
        super().__init__(
            session=session,
            model_class=DBPlaylist,
            mapper=PlaylistMapper(),
        )
        # Initialize individual track repositories following Clean Architecture
        self.track_repository = CoreTrackRepository(session)
        self.connector_repository = TrackConnectorRepository(session)

    # -------------------------------------------------------------------------
    # ENHANCED QUERY METHODS
    # -------------------------------------------------------------------------

    def select_with_relations(self) -> Select:
        """Create select statement with standard relations loaded."""
        return self.with_playlist_relationships(self.select())

    def select_by_connector(self, connector: str, connector_id: str) -> Select:
        """Build a query to fetch playlist by connector ID."""
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
        """Add standard playlist relationship loading."""
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
        """Save tracks that don't have IDs yet and return updated tracks."""
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
        """Manage playlist track associations with optimized database operations.

        A unified helper method for creating, updating, or modifying playlist tracks.
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
        """Manage connector mappings with optimized database operations.

        A unified helper method for creating or updating connector mappings.
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
        """Generate lexicographically sortable key."""
        return f"a{position:08d}"

    @staticmethod
    def _determine_source_connector(connector_ids: dict[str, str]) -> str | None:
        """Determine the source connector from a set of connector IDs."""
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
        """Get playlist by internal ID with all relationships."""
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
        """Get a playlist by its connector ID."""
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
        """Save playlist and all its tracks atomically."""
        if not playlist.name:
            raise ValueError("Playlist must have a name")

        # Execute in a transaction using base repository's helper
        return await self.execute_transaction(
            lambda: self._save_playlist_impl(playlist)
        )

    async def _save_playlist_impl(self, playlist: Playlist) -> Playlist:
        """Implementation to create a new playlist with tracks."""
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
        """Update existing playlist."""
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
        """Implementation for updating an existing playlist."""
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

    @db_operation("get_or_create")
    async def get_or_create(
        self,
        lookup_attrs: dict[str, Any],
        create_attrs: dict[str, Any] | None = None,
    ) -> tuple[Playlist, bool]:
        """Find a playlist by attributes or create it."""
        # Leverage base repository's find_one_by method
        conditions = {
            k: v for k, v in lookup_attrs.items() if hasattr(self.model_class, k)
        }

        existing = await self.find_one_by(conditions)
        if existing:
            return existing, False

        # Create new playlist
        all_attrs = {**lookup_attrs}
        if create_attrs:
            all_attrs.update(create_attrs)

        # Validate name
        if "name" not in all_attrs or not all_attrs["name"]:
            raise ValueError("Playlist requires a name")

        # Create the playlist domain object
        playlist = Playlist(
            name=all_attrs["name"],
            description=all_attrs.get("description"),
            tracks=all_attrs.get("tracks", []),
            connector_playlist_ids=all_attrs.get("connector_playlist_ids", {}),
        )

        # Check if we need to save tracks too
        if playlist.tracks:
            # Save with tracks
            created_playlist = await self.save_playlist(playlist)
            return created_playlist, True
        else:
            # Save without tracks using base repository's create method
            created_playlist = await self.create(playlist)

            # Add connector mappings if present
            if playlist.connector_playlist_ids and created_playlist.id is not None:
                await self._manage_connector_mappings(
                    created_playlist.id,
                    playlist.connector_playlist_ids,
                    operation="create",
                )
                # Refresh to include mappings
                return await self.get_playlist_by_id(created_playlist.id), True

            return created_playlist, True

    @db_operation("playlist delete")
    async def delete_playlist(self, playlist_id: int) -> bool:
        """Delete playlist by ID using soft deletion.

        Args:
            playlist_id: Internal playlist ID to delete

        Returns:
            True if playlist was deleted, False if it didn't exist
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
        """Soft delete all relations for a playlist.

        Args:
            playlist_id: Playlist ID whose relations to delete
            deletion_time: Timestamp for deletion
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
