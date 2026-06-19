"""Playlist, connector-link, and assignment repository protocols.

Split from the former monolithic ``interfaces.py``.
"""

from collections.abc import Awaitable, Mapping, Sequence
from typing import Protocol
from uuid import UUID

from src.domain.entities import (
    Playlist,
    PlaylistLink,
)
from src.domain.entities.playlist_assignment import (
    PlaylistAssignment,
    PlaylistAssignmentMember,
)
from src.domain.entities.playlist_link import SyncDirection, SyncStatus


class PlaylistRepositoryProtocol(Protocol):
    """Repository interface for playlist persistence operations."""

    def get_playlist_by_id(
        self, playlist_id: UUID, *, user_id: str
    ) -> Awaitable[Playlist]:
        """Get playlist by ID. Returns NotFoundError if wrong user (IDOR prevention)."""
        ...

    def save_playlist(self, playlist: Playlist) -> Awaitable[Playlist]:
        """Save playlist."""
        ...

    def get_playlist_by_connector(
        self,
        connector: str,
        connector_id: str,
        *,
        user_id: str,
        raise_if_not_found: bool = True,
    ) -> Awaitable[Playlist | None]:
        """Get playlist by connector ID."""
        ...

    def update_playlist(
        self, playlist_id: UUID, playlist: Playlist, *, user_id: str
    ) -> Awaitable[Playlist]:
        """Update existing playlist, verifying ownership."""
        ...

    def delete_playlist(self, playlist_id: UUID, *, user_id: str) -> Awaitable[bool]:
        """Delete playlist by ID, verifying ownership.

        Args:
            playlist_id: Internal playlist ID to delete
            user_id: Owner's user ID for ownership verification

        Returns:
            True if playlist was deleted, False if it didn't exist
        """
        ...

    def list_all_playlists(self, *, user_id: str) -> Awaitable[list[Playlist]]:
        """Get all playlists with basic metadata for listing.

        Returns playlists with minimal relationship loading for efficient
        listing operations. Suitable for CLI display and management interfaces.

        Returns:
            List of user's stored playlists with basic metadata
        """
        ...

    def get_playlists_for_track(
        self, track_id: UUID, *, user_id: str
    ) -> Awaitable[list[Playlist]]:
        """Get all playlists containing a specific track.

        Args:
            track_id: Internal track ID.

        Returns:
            List of playlists that contain the given track.
        """
        ...


class PlaylistLinkRepositoryProtocol(Protocol):
    """Repository interface for playlist link (mapping) operations."""

    def get_links_for_playlist(
        self, playlist_id: UUID
    ) -> Awaitable[list[PlaylistLink]]:
        """Get all connector links for a canonical playlist."""
        ...

    def list_by_user_connector(
        self, user_id: str, connector_name: str
    ) -> Awaitable[list[PlaylistLink]]:
        """Every playlist link for a given user on a given connector.

        Used by the Spotify browser to compute per-playlist import status
        (not-imported / imported / mapped) via set lookup against
        ``connector_playlist_identifier``.
        """
        ...

    def get_link(self, link_id: UUID) -> Awaitable[PlaylistLink | None]:
        """Get a single playlist link by ID."""
        ...

    def create_link(self, link: PlaylistLink) -> Awaitable[PlaylistLink]:
        """Create a new playlist link. Ensures the DBConnectorPlaylist exists."""
        ...

    def create_links_batch(
        self, links: Sequence[PlaylistLink]
    ) -> Awaitable[list[PlaylistLink]]:
        """Bulk-insert N playlist links. Returns only links actually inserted;
        duplicates (by (playlist_id, connector_name)) are skipped silently."""
        ...

    def update_sync_status(
        self,
        link_id: UUID,
        status: SyncStatus,
        *,
        error: str | None = None,
        tracks_added: int | None = None,
        tracks_removed: int | None = None,
        tracks_unmatched: int | None = None,
    ) -> Awaitable[None]:
        """Update the sync status and optional metrics for a link."""
        ...

    def update_link_direction(
        self, link_id: UUID, direction: SyncDirection
    ) -> Awaitable[PlaylistLink | None]:
        """Update the sync direction for a link. Returns the updated link, or None if not found."""
        ...

    def delete_link(self, link_id: UUID) -> Awaitable[bool]:
        """Delete a playlist link. Returns True if deleted."""
        ...


class PlaylistAssignmentRepositoryProtocol(Protocol):
    """Repository interface for playlist assignment persistence.

    Batch-first: single-item operations are the degenerate case. Assignments
    are created once and deleted individually; membership snapshots are
    replaced wholesale (DELETE-by-assignment + INSERT) on every apply.
    """

    def list_for_user(self, *, user_id: str) -> Awaitable[list[PlaylistAssignment]]:
        """All assignments for a user, across every connector playlist."""
        ...

    def list_for_ids(
        self, assignment_ids: Sequence[UUID], *, user_id: str
    ) -> Awaitable[list[PlaylistAssignment]]:
        """Fetch the given subset of assignments in one round-trip."""
        ...

    def list_for_connector_playlist(
        self, connector_playlist_id: UUID, *, user_id: str
    ) -> Awaitable[list[PlaylistAssignment]]:
        """All assignments bound to one connector playlist (may have many)."""
        ...

    def list_for_connector_playlist_ids(
        self, connector_playlist_ids: Sequence[UUID], *, user_id: str
    ) -> Awaitable[dict[UUID, list[PlaylistAssignment]]]:
        """Batch-fetch assignments for many connector playlists in one query.

        Returns ``{connector_playlist_id: [assignments]}`` — playlists with no
        assignments are absent from the result. Powers the Spotify picker's
        per-row badge / overflow-menu state without N+1 calls.
        """
        ...

    def create_assignments(
        self, assignments: Sequence[PlaylistAssignment], *, user_id: str
    ) -> Awaitable[list[PlaylistAssignment]]:
        """Insert assignments. UNIQUE on (connector_playlist_id, action_type, action_value)."""
        ...

    def delete_assignment(
        self, assignment_id: UUID, *, user_id: str
    ) -> Awaitable[bool]:
        """Delete one assignment. Returns True if a row was removed."""
        ...

    def get_members_for_assignments(
        self, assignment_ids: Sequence[UUID], *, user_id: str
    ) -> Awaitable[dict[UUID, list[PlaylistAssignmentMember]]]:
        """Batch member load for many assignments in one query."""
        ...

    def replace_members_for_assignments(
        self,
        snapshots: Mapping[UUID, Sequence[PlaylistAssignmentMember]],
        *,
        user_id: str,
    ) -> Awaitable[int]:
        """Batch member replace for many assignments: one DELETE + one INSERT."""
        ...
