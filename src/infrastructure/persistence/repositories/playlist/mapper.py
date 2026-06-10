"""Playlist repository mappers for domain-persistence conversions."""

from collections.abc import Sequence
from typing import override

from attrs import define
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.interfaces import ORMOption

from src.config import get_logger
from src.domain.entities import (
    Artist,
    ConnectorPlaylist,
    ConnectorPlaylistItem,
    Playlist,
    PlaylistEntry,
    Track,
    ensure_utc,
)
from src.infrastructure.persistence.database.db_models import (
    DBConnectorPlaylist,
    DBConnectorTrack,
    DBPlaylist,
    DBPlaylistMapping,
    DBPlaylistTrack,
    DBTrack,
    DBTrackMapping,
)
from src.infrastructure.persistence.repositories.base_repo import BaseModelMapper
from src.infrastructure.persistence.repositories.track.mapper import (
    extract_db_artist_names,
)

# Create module logger
logger = get_logger(__name__)


@define(frozen=True, slots=True)
class PlaylistMapper(BaseModelMapper[DBPlaylist, Playlist]):
    """Bidirectional mapper between domain and persistence models."""

    @override
    @staticmethod
    def get_default_relationships() -> Sequence[ORMOption]:
        """Eager-load the full chain ``to_domain`` traverses.

        Single source of truth for both the explicit (``get_playlist_by_id`` via
        ``with_playlist_relationships``) and inherited (``get_by_id`` via
        ``with_default_relationships``) load paths. A deep ``selectinload`` chain so
        the mapper reads only materialized state and never lazy-loads.
        """
        return [
            selectinload(DBPlaylist.mappings).selectinload(
                DBPlaylistMapping.connector_playlist
            ),
            selectinload(DBPlaylist.tracks)
            .selectinload(DBPlaylistTrack.track)
            .selectinload(DBTrack.mappings)
            .selectinload(DBTrackMapping.connector_track),
        ]

    @override
    @staticmethod
    async def to_domain(db_model: DBPlaylist) -> Playlist:
        """Convert persistence model to domain entity.

        A pure transformation over eager-loaded state: every relationship is read
        through ``loaded_list``/``loaded_one`` (zero I/O), so this awaits nothing.
        ``get_default_relationships`` guarantees the chain is materialized; an
        unloaded relationship degrades to ``[]``/``None`` rather than emitting a
        lazy query.
        """
        playlist_entries: list[PlaylistEntry] = []

        # Tracks are ordered by their lexicographic sort_key.
        for pt in sorted(
            db_model.loaded_list(DBPlaylist.tracks, DBPlaylistTrack),
            key=lambda pt: pt.sort_key,
        ):
            track = pt.loaded_one(DBPlaylistTrack.track, DBTrack)
            if track is None:
                continue

            # Build connector_track_identifiers from the track's connector mappings.
            connector_track_identifiers: dict[str, str] = {}
            for m in track.loaded_list(DBTrack.mappings, DBTrackMapping):
                ct = m.loaded_one(DBTrackMapping.connector_track, DBConnectorTrack)
                if ct is not None:
                    connector_track_identifiers[ct.connector_name] = (
                        ct.connector_track_identifier
                    )

            artist_names = extract_db_artist_names(track.artists)
            # Track._validate_artists raises on empty artists; skip silently so one
            # malformed track does not abort mapping the whole playlist.
            if not artist_names:
                continue

            domain_track = Track(
                id=track.id,
                version=track.version,
                user_id=track.user_id,
                title=track.title,
                artists=[Artist(name=name) for name in artist_names],
                album=track.album,
                duration_ms=track.duration_ms,
                release_date=ensure_utc(track.release_date),
                isrc=track.isrc,
                connector_track_identifiers=connector_track_identifiers,
            )

            # Only added_at is persisted; added_by would require a schema change.
            playlist_entries.append(
                PlaylistEntry(track=domain_track, added_at=pt.added_at, added_by=None)
            )

        # Build connector_playlist_identifiers from the playlist's connector mappings.
        connector_playlist_identifiers: dict[str, str] = {}
        for m in db_model.loaded_list(DBPlaylist.mappings, DBPlaylistMapping):
            cp = m.loaded_one(DBPlaylistMapping.connector_playlist, DBConnectorPlaylist)
            if cp is not None:
                connector_playlist_identifiers[m.connector_name] = (
                    cp.connector_playlist_identifier
                )

        return Playlist(
            id=db_model.id,
            name=db_model.name,
            user_id=db_model.user_id,
            description=db_model.description,
            entries=playlist_entries,
            connector_playlist_identifiers=connector_playlist_identifiers,
            updated_at=db_model.updated_at,
            track_count=len(playlist_entries),
        )

    @override
    @staticmethod
    def to_db(domain_model: Playlist) -> DBPlaylist:
        """Convert domain entity to persistence values."""
        playlist = DBPlaylist()
        playlist.id = domain_model.id
        playlist.user_id = domain_model.user_id
        playlist.name = domain_model.name
        playlist.description = domain_model.description
        playlist.track_count = len(domain_model.tracks) if domain_model.tracks else 0
        return playlist


@define(frozen=True, slots=True)
class ConnectorPlaylistMapper(BaseModelMapper[DBConnectorPlaylist, ConnectorPlaylist]):
    """Maps between DBConnectorPlaylist and ConnectorPlaylist domain model."""

    @override
    @staticmethod
    async def to_domain(db_model: DBConnectorPlaylist) -> ConnectorPlaylist:
        """Convert DB connector playlist to domain model.

        ``db_model.items`` is a JSONB list[JsonDict] — each entry has the shape
        ``{"connector_track_identifier": str, "position": int, "added_at": str|None,
        "added_by_id": str|None, "extras": dict}``. JsonValue is a union, so each
        field is narrowed defensively at the boundary.
        """
        items: list[ConnectorPlaylistItem] = []
        for item_dict in db_model.items:
            ident = item_dict.get("connector_track_identifier")
            position = item_dict.get("position")
            if not isinstance(ident, str) or not isinstance(position, int):
                continue
            added_at = item_dict.get("added_at")
            added_by_id = item_dict.get("added_by_id")
            extras = item_dict.get("extras", {})
            items.append(
                ConnectorPlaylistItem(
                    connector_track_identifier=ident,
                    position=position,
                    added_at=added_at if isinstance(added_at, str) else None,
                    added_by_id=added_by_id if isinstance(added_by_id, str) else None,
                    extras=extras if isinstance(extras, dict) else {},
                )
            )

        return ConnectorPlaylist(
            id=db_model.id,
            connector_name=db_model.connector_name,
            connector_playlist_identifier=db_model.connector_playlist_identifier,
            name=db_model.name,
            description=db_model.description,
            owner=db_model.owner,
            owner_id=db_model.owner_id,
            is_public=db_model.is_public,
            collaborative=db_model.collaborative,
            follower_count=db_model.follower_count,
            raw_metadata=db_model.raw_metadata,
            snapshot_id=db_model.snapshot_id,
            items=items,
            last_updated=db_model.last_updated,
        )

    @staticmethod
    def to_values_dict(domain_model: ConnectorPlaylist) -> dict[str, object]:
        """Convert domain model to a column-name-keyed dict for bulk inserts.

        Single source of truth for the column → value mapping; ``to_db``
        now delegates here so a new field added on ``DBConnectorPlaylist``
        only needs to be threaded through one place.
        """
        return {
            "id": domain_model.id,
            "connector_name": domain_model.connector_name,
            "connector_playlist_identifier": domain_model.connector_playlist_identifier,
            "name": domain_model.name,
            "description": domain_model.description,
            "owner": domain_model.owner,
            "owner_id": domain_model.owner_id,
            "is_public": domain_model.is_public,
            "collaborative": domain_model.collaborative,
            "follower_count": domain_model.follower_count,
            "raw_metadata": domain_model.raw_metadata,
            "snapshot_id": domain_model.snapshot_id,
            "items": [
                {
                    "connector_track_identifier": item.connector_track_identifier,
                    "position": item.position,
                    "added_at": item.added_at,
                    "added_by_id": item.added_by_id,
                    "extras": item.extras,
                }
                for item in domain_model.items
            ],
            "last_updated": domain_model.last_updated,
        }

    @override
    @staticmethod
    def to_db(domain_model: ConnectorPlaylist) -> DBConnectorPlaylist:
        """Convert domain model to DB connector playlist."""
        return DBConnectorPlaylist(
            **ConnectorPlaylistMapper.to_values_dict(domain_model)
        )

    @override
    @staticmethod
    def get_default_relationships() -> list[str]:
        """Get default relationships to load for connector playlists."""
        return []
