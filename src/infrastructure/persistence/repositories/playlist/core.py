"""Database operations for playlists, tracks, and external service mappings.

Handles CRUD operations for playlists from multiple music services (Spotify, Last.fm,
MusicBrainz), maintaining track ordering and synchronizing external IDs.
"""

from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID

import attrs
from sqlalchemy import Select, delete, insert, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import get_logger
from src.config.constants import ConnectorPriority
from src.domain.entities import (
    ConnectorTrack,
    Playlist,
    PlaylistEntry,
    Track,
)
from src.infrastructure.persistence.database.db_models import (
    DBConnectorPlaylist,
    DBConnectorTrack,
    DBPlaylist,
    DBPlaylistMapping,
    DBPlaylistTrack,
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

        Thin delegate to ``PlaylistMapper.get_default_relationships`` so the
        explicit get paths and the inherited ``get_by_id`` path share one loader
        definition (single source of truth for the eager-load chain).
        """
        return self.with_relationship(stmt, *self.mapper.get_default_relationships())

    # -------------------------------------------------------------------------
    # HELPER METHODS (non-decorated)
    # -------------------------------------------------------------------------

    async def _save_new_tracks(
        self,
        tracks: list[Track],
        connector: str | None = None,
        *,
        user_id: str,
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
            if track.version > 0:
                # Track already persisted (version > 0), no processing needed
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
                        connector, connector_tracks_to_save, user_id=user_id
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
        playlist_id: UUID,
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
        connector_id_by_ref = await self._resolve_connector_track_ids(entries)

        # Build one row per entry — resolved (track_id) OR unresolved
        # (connector_track_id + unresolved_metadata). Every source position
        # becomes a row, so the playlist is always complete.
        values = [
            row
            for idx, entry in enumerate(entries)
            if (
                row := self._build_track_values(
                    playlist_id, entry, idx, connector_id_by_ref, now
                )
            )
            is not None
        ]

        if values:
            _ = await self.session.execute(insert(DBPlaylistTrack).values(values))
            await self.session.flush()

    async def _update_playlist_tracks(
        self,
        playlist_id: UUID,
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

        connector_id_by_ref = await self._resolve_connector_track_ids(entries)

        # Two consumption views over the same records:
        # - records_by_id: address one exact membership record when the entry
        #   carries its DB id (identity-preserving reorder/remove).
        # - available_records: FIFO pool per membership key for entries with no
        #   id match. The key is "t:<track_id>" for resolved rows and
        #   "c:<connector>:<identifier>" for unresolved ones, so an unresolved
        #   position keeps its slot (and added_at) across re-pulls too.
        records_by_id: dict[UUID, DBPlaylistTrack] = {
            record.id: record for record in existing_records
        }
        available_records: defaultdict[str, list[DBPlaylistTrack]] = defaultdict(list)
        for record in existing_records:
            key = self._record_key(record)
            if key is not None:
                available_records[key].append(record)

        logger.debug(
            f"Playlist update: {len(existing_records)} existing records, "
            + f"{len(available_records)} unique memberships, "
            + f"{len(entries)} target entries"
        )

        consumed_ids: set[UUID] = set()
        records_to_update: list[DBPlaylistTrack] = []
        records_to_create: list[DBPlaylistTrack] = []

        # Consume records for each target position
        for idx, entry in enumerate(entries):
            key = self._entry_key(entry)
            record = self._consume_record_for_entry(
                entry.id, key, records_by_id, available_records, consumed_ids
            )
            if record is not None:
                # CONSUME the matched record — preserves its id and added_at,
                # updating position (sort_key) only.
                consumed_ids.add(record.id)
                record.sort_key = self._generate_sort_key(idx)
                record.updated_at = now
                records_to_update.append(record)
            else:
                # No existing record for this membership — create one (resolved
                # or unresolved). May be None only when an unresolved entry's
                # connector track can't be located (logged, see _build_track_values).
                new_values = self._build_track_values(
                    playlist_id, entry, idx, connector_id_by_ref, now
                )
                if new_values is not None:
                    records_to_create.append(DBPlaylistTrack(**new_values))

        # Any record not consumed (by id or by FIFO) was removed from the playlist
        records_to_delete: list[DBPlaylistTrack] = [
            record for record in existing_records if record.id not in consumed_ids
        ]

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
        playlist_id: UUID,
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
    ) -> dict[str, UUID]:
        """Ensure connector playlists exist and return their database IDs.

        Creates minimal connector playlist entries if they don't exist yet,
        allowing mappings to work before full playlist sync.

        Args:
            connector_ids: Map of service names to external playlist IDs.

        Returns:
            Map of connector names to their database IDs.
        """

        now = datetime.now(UTC)
        connector_playlist_db_ids: dict[str, UUID] = {}

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
        self, playlist_id: UUID, connector_ids: dict[str, str], *, user_id: str
    ) -> None:
        """Create initial connector mappings for a new playlist.

        Uses bulk insert for optimal performance with new playlists.

        Args:
            playlist_id: Internal database playlist ID.
            connector_ids: Map of service names to external playlist IDs.
            user_id: Owner of the canonical playlist. Stored on each mapping
                so ``uq_user_connector_playlist`` correctly scopes per user.
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
                "user_id": user_id,
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
            # Defense-in-depth: silently skip if this (user, external playlist)
            # pair is already mapped (uq_user_connector_playlist). The
            # natural-identity probe in _save_playlist_impl should normally
            # route us to the update path; ON CONFLICT guarantees idempotency
            # if the probe misses for any reason (e.g. concurrent save).
            _ = await self.session.execute(
                pg_insert(DBPlaylistMapping)
                .values(values)
                .on_conflict_do_nothing(constraint="uq_user_connector_playlist")
            )
            await self.session.flush()

    async def _update_connector_mappings(
        self, playlist_id: UUID, connector_ids: dict[str, str], *, user_id: str
    ) -> None:
        """Update connector mappings for an existing playlist.

        Checks existing mappings, updates changed ones, and creates new ones.

        Args:
            playlist_id: Internal database playlist ID.
            connector_ids: Map of service names to external playlist IDs.
            user_id: Owner of the canonical playlist. Stamped onto any new
                mapping rows created here.
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
                    "user_id": user_id,
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
            # See _create_connector_mappings for the ON CONFLICT rationale.
            _ = await self.session.execute(
                pg_insert(DBPlaylistMapping)
                .values(new_mappings)
                .on_conflict_do_nothing(constraint="uq_user_connector_playlist")
            )

        await self.session.flush()

    async def _manage_connector_mappings(
        self,
        playlist_id: UUID,
        connector_ids: dict[str, str],
        is_update: bool,
        *,
        user_id: str,
    ) -> None:
        """Manage connector mappings (delegates to focused methods).

        Args:
            playlist_id: Internal database playlist ID.
            connector_ids: Map of service names to external playlist IDs.
            is_update: True if updating existing playlist, False if creating new.
            user_id: Owner of the canonical playlist.
        """
        if is_update:
            await self._update_connector_mappings(
                playlist_id, connector_ids, user_id=user_id
            )
        else:
            await self._create_connector_mappings(
                playlist_id, connector_ids, user_id=user_id
            )

    # -------------------------------------------------------------------------
    # UTILITY METHODS
    # -------------------------------------------------------------------------

    @staticmethod
    def _record_key(record: DBPlaylistTrack) -> str | None:
        """Membership key for a stored row, by canonical or external identity.

        Resolved rows key on the canonical track id; unresolved rows key on the
        external ``connector:identifier`` read from their display snapshot — so an
        unresolved position keeps its slot across re-pulls without depending on
        the optional ``connector_track_id`` FK (which may be NULL).
        """
        if record.track_id is not None:
            return f"t:{record.track_id}"
        meta = record.unresolved_metadata
        if isinstance(meta, dict):
            name = meta.get("connector_name")
            identifier = meta.get("connector_track_identifier")
            if isinstance(name, str) and isinstance(identifier, str):
                return f"c:{name}:{identifier}"
        return None

    @staticmethod
    def _entry_key(entry: PlaylistEntry) -> str | None:
        """Membership key for an incoming entry, mirroring ``_record_key``."""
        if entry.track is not None:
            return f"t:{entry.track.id}"
        ref = entry.connector_track_ref
        if ref is not None:
            return f"c:{ref.connector_name}:{ref.connector_track_identifier}"
        return None

    @staticmethod
    def _consume_record_for_entry(
        entry_id: UUID,
        key: str | None,
        records_by_id: dict[UUID, DBPlaylistTrack],
        available_records: defaultdict[str, list[DBPlaylistTrack]],
        consumed_ids: set[UUID],
    ) -> DBPlaylistTrack | None:
        """Pick the existing membership record this entry should reuse.

        Entry-identity first: if the entry carries the id of an existing record
        with the same membership key, reuse that exact record — this is what
        makes reorder/remove preserve record id + added_at when the caller
        passes loaded entries. Otherwise fall back to FIFO consumption from the
        membership-key pool (entries built in memory or sourced from a TrackList
        carry fresh ids that never match a DB record). Returns None when nothing
        is available, signalling the caller to create a new membership record.
        """
        # 1. Precise membership match by id (same membership key only).
        candidate = records_by_id.get(entry_id)
        if (
            candidate is not None
            and candidate.id not in consumed_ids
            and PlaylistRepository._record_key(candidate) == key
        ):
            return candidate

        # 2. FIFO fallback within the membership-key pool, skipping consumed.
        if key is None:
            return None
        pool = available_records[key]
        while pool:
            candidate = pool.pop(0)
            if candidate.id not in consumed_ids:
                return candidate
        return None

    async def _resolve_connector_track_ids(
        self, entries: list[PlaylistEntry]
    ) -> dict[tuple[str, str], UUID]:
        """Best-effort map of each unresolved entry's connector ref → DBConnectorTrack id.

        Populates the optional ``connector_track_id`` FK for efficient
        re-resolution. The domain ref carries only the external
        ``(connector_name, identifier)``, so resolve the FK ids in one batch
        query. A miss is fine — the position still persists via
        ``unresolved_metadata`` with a NULL FK (e.g. Spotify local/unavailable
        tracks that have no ``connector_tracks`` row at all).
        """
        refs = {
            (
                entry.connector_track_ref.connector_name,
                entry.connector_track_ref.connector_track_identifier,
            )
            for entry in entries
            if entry.track is None and entry.connector_track_ref is not None
        }
        if not refs:
            return {}
        stmt = select(DBConnectorTrack).where(
            tuple_(
                DBConnectorTrack.connector_name,
                DBConnectorTrack.connector_track_identifier,
            ).in_(list(refs))
        )
        rows = (await self.session.scalars(stmt)).all()
        return {
            (row.connector_name, row.connector_track_identifier): row.id for row in rows
        }

    def _build_track_values(
        self,
        playlist_id: UUID,
        entry: PlaylistEntry,
        idx: int,
        connector_id_by_ref: dict[tuple[str, str], UUID],
        now: datetime,
    ) -> dict[str, object] | None:
        """Build the column values for one playlist_tracks row.

        Resolved entry → ``track_id`` set. Unresolved entry → ``track_id`` NULL
        with the always-present ``unresolved_metadata`` snapshot and a best-effort
        ``connector_track_id`` FK (NULL when no connector_tracks row exists). The
        unresolved row therefore always persists — never a silent drop. Returns
        None only for a malformed entry that is neither resolved nor carries a
        connector ref (a programming error that can't satisfy the CHECK).
        """
        # Keep all rows homogeneous (same keys) so a bulk insert — which derives
        # its column set from the first dict — never drops the unresolved columns.
        base: dict[str, object] = {
            "playlist_id": playlist_id,
            "sort_key": self._generate_sort_key(idx),
            "added_at": entry.added_at,
            "created_at": now,
            "updated_at": now,
            "track_id": None,
            "connector_track_id": None,
            "unresolved_metadata": None,
        }
        if entry.track is not None:
            return {**base, "track_id": entry.track.id}

        ref = entry.connector_track_ref
        if ref is None:
            logger.error(
                "Playlist entry is neither resolved nor carries a connector ref; "
                "skipping position",
                playlist_id=playlist_id,
                position=idx,
            )
            return None
        return {
            **base,
            # FK is best-effort: NULL when the connector track has no DB row.
            "connector_track_id": connector_id_by_ref.get((
                ref.connector_name,
                ref.connector_track_identifier,
            )),
            "unresolved_metadata": ref.to_metadata(),
        }

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
        for connector in ConnectorPriority.ORDER:
            if connector in connector_ids:
                return connector
        return None

    # -------------------------------------------------------------------------
    # PUBLIC API METHODS (decorated)
    # -------------------------------------------------------------------------

    @db_operation("get_playlist_by_id")
    async def get_playlist_by_id(
        self,
        playlist_id: UUID,
        *,
        user_id: str,
    ) -> Playlist:
        """Retrieve playlist with all tracks and external service mappings.

        Args:
            playlist_id: Internal database ID.
            user_id: Owner's user ID for ownership verification.

        Returns:
            Complete playlist entity with tracks and mappings.

        Raises:
            NotFoundError: If playlist not found or belongs to another user.
        """
        stmt = self.select_by_id(playlist_id).where(DBPlaylist.user_id == user_id)
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
        *,
        user_id: str,
        raise_if_not_found: bool = True,
    ) -> Playlist | None:
        """Find playlist by external service ID (Spotify, Last.fm, etc.).

        Args:
            connector: Service name (spotify, lastfm, musicbrainz).
            connector_id: External playlist identifier.
            user_id: Owner's user ID for scoping.
            raise_if_not_found: Whether to raise exception if not found.

        Returns:
            Playlist entity or None if not found and raise_if_not_found=False.

        Raises:
            NotFoundError: If playlist not found and raise_if_not_found=True.
        """
        # Use the enhanced select method, scoped to user
        stmt = self.select_by_connector(connector, connector_id).where(
            DBPlaylist.user_id == user_id
        )

        # Add eager loading with our helper
        stmt = self.with_playlist_relationships(stmt)

        # Execute query using base repository method
        db_model = await self.execute_select_one(stmt)

        if not db_model:
            if raise_if_not_found:
                from src.domain.exceptions import NotFoundError

                raise NotFoundError(
                    f"Playlist for {connector}:{connector_id} not found"
                )
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

        Detects create vs update on the *natural* identity for externally
        sourced playlists — ``(user_id, connector, connector_playlist_id)``
        — falling back to the synthetic local UUID otherwise. The probe
        queries ``playlist_mappings`` directly rather than going through
        ``get_playlist_by_connector``'s 3-table JOIN through ``DBPlaylist``,
        because the ``uq_user_connector_playlist`` UNIQUE constraint we're
        trying not to violate lives on ``playlist_mappings``, not on
        ``playlists``. Probing the constraint source is both more direct
        and immune to stale identity-map state on the session.

        The probe scopes by ``user_id`` to mirror the constraint exactly:
        two users importing the same external playlist URL each get their
        own canonical row.
        """
        # Determine source connector if available
        source_connector = self._determine_source_connector(
            playlist.connector_playlist_identifiers
        )

        # Natural-identity lookup: if a mapping already exists for this
        # (user_id, connector, connector_playlist_id), reuse its playlist_id
        # so the downstream code takes the update path. Probes the mapping
        # table directly to ensure we always see the constraint's perspective.
        if source_connector:
            connector_id = playlist.connector_playlist_identifiers[source_connector]
            mapping_stmt = (
                select(DBPlaylistMapping.playlist_id)
                .join(
                    DBConnectorPlaylist,
                    DBPlaylistMapping.connector_playlist_id == DBConnectorPlaylist.id,
                )
                .where(
                    DBPlaylistMapping.user_id == playlist.user_id,
                    DBPlaylistMapping.connector_name == source_connector,
                    DBConnectorPlaylist.connector_playlist_identifier == connector_id,
                )
            )
            existing_playlist_id = (
                await self.session.execute(mapping_stmt)
            ).scalar_one_or_none()
            if existing_playlist_id is not None:
                playlist = attrs.evolve(playlist, id=existing_playlist_id)

        # Detect create vs update by checking if entity exists in DB
        existing = await self.execute_select_one(self.select_by_id(playlist.id))
        is_update = existing is not None

        # Save tracks first with source connector for proper mappings. Only
        # RESOLVED entries carry a canonical track to persist; unresolved
        # positions are carried through untouched (track stays None).
        resolved_positions = [
            i for i, e in enumerate(playlist.entries) if e.track is not None
        ]
        tracks_to_save = [
            track
            for i in resolved_positions
            if (track := playlist.entries[i].track) is not None
        ]
        updated_tracks = await self._save_new_tracks(
            tracks_to_save,
            connector=source_connector,
            user_id=playlist.user_id,
        )
        position_to_saved_track = {
            pos: updated_tracks[j] for j, pos in enumerate(resolved_positions)
        }

        # Rebuild entries with persisted tracks (preserving added_at metadata
        # AND the membership id, so entry-identity matching in
        # _update_playlist_tracks can address the existing DB record directly).
        # Unresolved entries pass through unchanged so their position is kept.
        updated_entries = [
            attrs.evolve(entry, track=position_to_saved_track[idx])
            if idx in position_to_saved_track
            else entry
            for idx, entry in enumerate(playlist.entries)
        ]

        # Create or update the playlist DB entity
        playlist_id: UUID
        if is_update:
            # Update existing playlist
            actual_track_count = len(updated_entries)

            updates = {
                "name": playlist.name,
                "description": playlist.description,
                "track_count": actual_track_count,
                "updated_at": datetime.now(UTC),
            }

            _ = await self.session.execute(
                update(self.model_class)
                .where(
                    self.model_class.id == playlist.id,
                    self.model_class.user_id == playlist.user_id,
                )
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
            user_id=playlist.user_id,
        )

        # Flush changes to ensure they're visible to subsequent queries
        await self.session.flush()

        # Return a fresh copy with all relationships eager-loaded
        return await self.get_playlist_by_id(playlist_id, user_id=playlist.user_id)

    @db_operation("playlist delete")
    async def delete_playlist(self, playlist_id: UUID, *, user_id: str) -> bool:
        """Delete playlist and all related tracks/mappings, verifying ownership.

        Args:
            playlist_id: Internal playlist ID to delete.
            user_id: Owner's user ID for ownership verification.

        Returns:
            True if playlist was deleted, False if it didn't exist.
        """
        logger.info("Deleting playlist", playlist_id=playlist_id)

        # Hard delete the playlist (CASCADE will handle related records)
        result = await self.session.execute(
            delete(DBPlaylist)
            .where(DBPlaylist.id == playlist_id)
            .where(DBPlaylist.user_id == user_id)
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
    async def list_all_playlists(self, *, user_id: str) -> list[Playlist]:
        """Get all playlists with basic metadata for efficient listing.

        Lightweight query that skips track loading entirely. Eagerly loads
        only mappings → connector_playlist (3 total queries) and builds
        Playlist objects directly from DB scalar columns.

        Args:
            user_id: Owner's user ID for scoping.

        Returns:
            List of user's stored playlists with basic metadata
        """
        logger.debug("Listing all playlists", user_id=user_id)

        # Eagerly load mappings chain for connector identifiers — no track loading
        stmt = (
            self
            .select()
            .where(DBPlaylist.user_id == user_id)
            .options(
                selectinload(DBPlaylist.mappings).selectinload(
                    DBPlaylistMapping.connector_playlist
                ),
            )
            .order_by(DBPlaylist.updated_at.desc())
        )

        result = await self.session.execute(stmt)
        db_playlists = result.scalars().all()

        playlists = [self._build_lightweight_playlist(p) for p in db_playlists]
        logger.info("Retrieved playlists for listing", count=len(playlists))
        return playlists

    @db_operation("get_playlists_for_track")
    async def get_playlists_for_track(
        self, track_id: UUID, *, user_id: str
    ) -> list[Playlist]:
        """Get all playlists containing a specific track, scoped to user.

        Returns lightweight Playlist objects (no track loading) for display
        in track detail views.

        Args:
            track_id: Internal track ID.
            user_id: Owner's user ID for scoping.
        """
        stmt = (
            self
            .select()
            .join(DBPlaylistTrack, DBPlaylistTrack.playlist_id == DBPlaylist.id)
            .where(DBPlaylistTrack.track_id == track_id)
            .where(DBPlaylist.user_id == user_id)
            .distinct()
            .options(
                selectinload(DBPlaylist.mappings).selectinload(
                    DBPlaylistMapping.connector_playlist
                ),
            )
        )

        result = await self.session.execute(stmt)
        db_playlists = result.scalars().all()

        return [self._build_lightweight_playlist(p) for p in db_playlists]

    @db_operation("update_playlist")
    async def update_playlist(
        self, playlist_id: UUID, playlist: Playlist, *, user_id: str
    ) -> Playlist:
        """Update an existing playlist by ID, verifying ownership.

        Args:
            playlist_id: Internal database ID of the playlist to update.
            playlist: Playlist entity with updated data.
            user_id: Owner's user ID for ownership verification.

        Returns:
            Updated playlist with all relationships loaded.
        """
        return await self.save_playlist(
            attrs.evolve(playlist, id=playlist_id, user_id=user_id)
        )

    @staticmethod
    def _build_lightweight_playlist(db_playlist: DBPlaylist) -> Playlist:
        """Build a Playlist entity from eagerly-loaded DB model without full mapper."""
        connector_ids: dict[str, str] = {
            mapping.connector_name: mapping.connector_playlist.connector_playlist_identifier
            for mapping in db_playlist.mappings
        }

        return Playlist(
            id=db_playlist.id,
            name=db_playlist.name,
            description=db_playlist.description,
            connector_playlist_identifiers=connector_ids,
            updated_at=db_playlist.updated_at,
            track_count=db_playlist.track_count,
        )

    # _soft_delete_playlist_relations method removed - CASCADE handles related record deletion
