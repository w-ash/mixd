"""Playlist repository mappers for domain-persistence conversions."""

# pyright: reportExplicitAny=false, reportAttributeAccessIssue=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportAny=false
# Legitimate Any: SQLAlchemy JSON columns, dynamic relationship traversal via safe_fetch_relationship

from typing import override

from attrs import define

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
    DBPlaylist,
)
from src.infrastructure.persistence.repositories.base_repo import (
    BaseModelMapper,
    safe_fetch_relationship,
)
from src.infrastructure.persistence.repositories.track.mapper import TrackMapper

# Create module logger
logger = get_logger(__name__)


@define(frozen=True, slots=True)
class PlaylistMapper(BaseModelMapper[DBPlaylist, Playlist]):
    """Bidirectional mapper between domain and persistence models."""

    @override
    @staticmethod
    def get_default_relationships() -> list[str]:
        """Get default relationships to load for this model."""
        return ["mappings", "tracks"]

    @override
    @staticmethod
    async def to_domain(db_model: DBPlaylist) -> Playlist:
        """Convert persistence model to domain entity using a consistent async-safe approach."""
        # Process playlist entries - build PlaylistEntry with track + position metadata
        playlist_entries: list[PlaylistEntry] = []

        # Get playlist tracks using safe fetch relationship (always returns a list)
        playlist_tracks = await safe_fetch_relationship(db_model, "tracks")

        # Filter and sort active tracks (no soft delete filtering needed after hard delete migration)
        active_tracks = sorted(
            playlist_tracks,
            key=lambda pt: pt.sort_key if hasattr(pt, "sort_key") else 0,
        )

        # Process each playlist track to build PlaylistEntry
        for pt in active_tracks:
            # Get track consistently - safe_fetch_relationship always returns a list
            tracks = await safe_fetch_relationship(pt, "track")

            # Skip if no track was found
            if not tracks:
                continue

            # Get the first track from the list (to-one relationship)
            track = tracks[0]

            # Skip missing tracks (no soft delete filtering needed after hard delete migration)
            if not track:
                continue

            # Get track mappings - always returns a list
            track_mappings = await safe_fetch_relationship(track, "mappings")

            # Build connector_track_identifiers from mappings
            connector_track_identifiers: dict[str, str] = {}

            # Process track mappings (no soft delete filtering needed after hard delete migration)
            for m in track_mappings:
                # Get connector tracks - always returns a list
                try:
                    connector_tracks = await safe_fetch_relationship(
                        m, "connector_track"
                    )
                    if not connector_tracks:
                        continue

                    # Get the first connector track (to-one relationship)
                    connector_track = connector_tracks[0]

                    # Skip if missing required attributes (no soft delete filtering needed after hard delete migration)
                    if (
                        not connector_track
                        or not hasattr(connector_track, "connector_name")
                        or not hasattr(connector_track, "connector_track_identifier")
                    ):
                        continue

                    # Store connector track identifier
                    connector_track_identifiers[connector_track.connector_name] = (
                        connector_track.connector_track_identifier
                    )
                except Exception as e:
                    logger.debug(f"Error getting connector track: {e}")
                    continue

            # Skip tracks missing essential attributes
            if not all(hasattr(track, attr) for attr in ["id", "title", "artists"]):
                continue

            # Extract artist names using standardized method
            artist_names = TrackMapper.extract_artist_names(
                track.artists.get("names", [])
            )
            if not artist_names:
                continue

            # Create the track domain object
            domain_track = Track(
                id=track.id,
                version=track.version,
                user_id=track.user_id,
                title=track.title,
                artists=[Artist(name=name) for name in artist_names],
                album=getattr(track, "album", None),
                duration_ms=getattr(track, "duration_ms", None),
                release_date=ensure_utc(getattr(track, "release_date", None)),
                isrc=getattr(track, "isrc", None),
                connector_track_identifiers=connector_track_identifiers,
            )

            # Extract position metadata from DBPlaylistTrack
            # NOTE: Only added_at is stored in DB. added_by would require schema changes.
            added_at = getattr(pt, "added_at", None)

            # Create PlaylistEntry with track + position metadata
            playlist_entry = PlaylistEntry(
                track=domain_track,
                added_at=added_at,
                added_by=None,  # Not stored in DB currently
            )
            playlist_entries.append(playlist_entry)

        # Get playlist mappings using safe fetch relationship (always returns a list)
        playlist_mappings = await safe_fetch_relationship(db_model, "mappings")

        # Process active playlist mappings
        connector_playlist_identifiers: dict[str, str] = {}
        for m in playlist_mappings:
            if hasattr(m, "connector_name") and hasattr(m, "connector_playlist_id"):
                # Get the connector playlist to extract the external identifier
                try:
                    connector_playlists = await safe_fetch_relationship(
                        m, "connector_playlist"
                    )
                    if connector_playlists:
                        connector_playlist = connector_playlists[0]
                        if hasattr(connector_playlist, "connector_playlist_identifier"):
                            connector_playlist_identifiers[m.connector_name] = (
                                connector_playlist.connector_playlist_identifier
                            )
                except Exception as e:
                    logger.debug(f"Error getting connector playlist: {e}")
                    continue

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
        """Convert DB connector playlist to domain model."""
        # Convert stored JSON items to ConnectorPlaylistItem objects
        items = [
            ConnectorPlaylistItem(
                connector_track_identifier=item_dict["connector_track_identifier"],
                position=item_dict["position"],
                added_at=item_dict.get("added_at"),
                added_by_id=item_dict.get("added_by_id"),
                extras=item_dict.get("extras", {}),
            )
            for item_dict in db_model.items
        ]

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
            items=items,
            last_updated=db_model.last_updated,
        )

    @override
    @staticmethod
    def to_db(domain_model: ConnectorPlaylist) -> DBConnectorPlaylist:
        """Convert domain model to DB connector playlist."""
        # Convert ConnectorPlaylistItem objects to serializable dictionaries
        items_dicts = [
            {
                "connector_track_identifier": item.connector_track_identifier,
                "position": item.position,
                "added_at": item.added_at,
                "added_by_id": item.added_by_id,
                "extras": item.extras,
            }
            for item in domain_model.items
        ]

        return DBConnectorPlaylist(
            id=domain_model.id,
            connector_name=domain_model.connector_name,
            connector_playlist_identifier=domain_model.connector_playlist_identifier,
            name=domain_model.name,
            description=domain_model.description,
            owner=domain_model.owner,
            owner_id=domain_model.owner_id,
            is_public=domain_model.is_public,
            collaborative=domain_model.collaborative,
            follower_count=domain_model.follower_count,
            raw_metadata=domain_model.raw_metadata,
            items=items_dicts,
            last_updated=domain_model.last_updated,
        )

    @override
    @staticmethod
    def get_default_relationships() -> list[str]:
        """Get default relationships to load for connector playlists."""
        return []
