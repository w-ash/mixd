"""Track repository + identity/merge service protocols.

Split from the former monolithic ``interfaces.py``.
"""

from collections.abc import Awaitable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Protocol, TypedDict
from uuid import UUID

from src.domain.entities import (
    Track,
)
from src.domain.entities.shared import SortKey
from src.domain.matching.types import (
    MatchResultsById,
    ProgressCallback,
    RawProviderMatch,
)

if TYPE_CHECKING:
    from src.domain.repositories.uow import UnitOfWorkProtocol


class TrackFacets(TypedDict):
    """Per-facet counts scoped to the currently-applied filters.

    Counts are *contextual with self* — every active filter (including the
    one being counted) is applied. Simpler and faster than peel-away-self
    semantics; revisit if user feedback demands the Algolia shape.
    """

    preference: dict[str, int]  # "star"|"yah"|"hmm"|"nah"|"unrated" → count
    liked: dict[str, int]  # "true"|"false" → count
    connector: dict[str, int]


class TrackListingPage(TypedDict):
    """Result shape for paginated track listing queries."""

    tracks: list[Track]
    total: int | None  # None when count was skipped (cursor-paginated pages)
    liked_track_ids: set[UUID]
    # Cursor value type depends on the active sort column: str (title,
    # artists_text), int (duration_ms), or datetime (created_at). The
    # application layer's PageCursor encodes/decodes for the wire.
    next_page_key: tuple[str | int | datetime | None, UUID] | None
    # Facet counts over the current filter set. None when not requested.
    facets: TrackFacets | None


class TrackRepositoryProtocol(Protocol):
    """Repository interface for track persistence operations."""

    def save_track(self, track: Track) -> Awaitable[Track]:
        """Save track."""
        ...

    def get_by_id(
        self, id_: UUID, load_relationships: list[str] | None = None
    ) -> Awaitable[Track]:
        """Get track by ID (unscoped — for infrastructure-internal use only)."""
        ...

    def get_track_by_id(
        self,
        track_id: UUID,
        *,
        user_id: str,
        load_relationships: list[str] | None = None,
    ) -> Awaitable[Track]:
        """Get track by ID, scoped to user. Raises NotFoundError if not found or wrong user."""
        ...

    def find_tracks_by_ids(self, track_ids: list[UUID]) -> Awaitable[dict[UUID, Track]]:
        """Find multiple tracks by their internal IDs in a single batch operation.

        Args:
            track_ids: List of internal track IDs to retrieve

        Returns:
            Dictionary mapping track IDs to Track objects
        """
        ...

    def move_references_to_track(self, from_id: UUID, to_id: UUID) -> Awaitable[None]:
        """Move all foreign key references (playlist tracks, plays, likes) from one track to another.

        Handles conflict resolution for likes where both tracks have entries
        for the same service (keeps the most recently synced state).

        Args:
            from_id: Source track ID whose references will be moved.
            to_id: Destination track ID that will receive the references.
        """
        ...

    def merge_mappings_to_track(self, from_id: UUID, to_id: UUID) -> Awaitable[None]:
        """Merge connector mappings from one track to another with conflict resolution.

        Handles two cases:
        - Same connector + same external ID: keep the higher-confidence mapping
        - Same connector + different external IDs: keep both, destination's stays primary

        Args:
            from_id: Source track ID whose mappings will be merged.
            to_id: Destination track ID that will receive the mappings.
        """
        ...

    def merge_metrics_to_track(self, from_id: UUID, to_id: UUID) -> Awaitable[None]:
        """Merge track metrics from one track to another with conflict resolution.

        For duplicate (connector_name, metric_type) pairs, keeps the most
        recently collected value.

        Args:
            from_id: Source track ID whose metrics will be merged.
            to_id: Destination track ID that will receive the metrics.
        """
        ...

    def hard_delete_track(self, track_id: UUID) -> Awaitable[None]:
        """Permanently delete a track record from the database.

        This bypasses soft-delete and removes the row entirely. Should only be
        used after all references have been moved away (e.g., during merge).

        Args:
            track_id: ID of the track to permanently delete.
        """
        ...

    def list_tracks(
        self,
        *,
        user_id: str,
        query: str | None = None,
        liked: bool | None = None,
        connector: str | None = None,
        preference: str | None = None,
        tags: Sequence[str] | None = None,
        tag_mode: Literal["and", "or"] = "and",
        namespace: str | None = None,
        sort_by: str = "title_asc",
        limit: int = 50,
        offset: int = 0,
        after_value: SortKey | None = None,
        after_id: UUID | None = None,
        include_total: bool = True,
        include_facets: bool = False,
    ) -> Awaitable[TrackListingPage]:
        """List tracks with optional search, filters, sorting, and pagination.

        Supports both offset-based and keyset (cursor) pagination. When
        ``after_value`` and ``after_id`` are provided, keyset pagination
        seeks directly to the next page in O(1). Falls back to OFFSET otherwise.

        Args:
            query: Text search across title, artist, album.
            liked: Filter by canonical liked status (liked on any service).
            connector: Filter by connector mapping presence.
            tags: Filter to tracks carrying the given tag(s). When set,
                ``tag_mode`` picks intersection ("and") or union ("or") semantics.
            tag_mode: Combine multi-tag filters. ``"and"`` returns tracks
                carrying every listed tag; ``"or"`` returns tracks carrying
                any listed tag. Ignored when ``tags`` is None.
            namespace: Filter to tracks carrying any tag whose namespace
                matches (e.g. ``"mood"`` → any ``mood:*`` tag).
            sort_by: Sort field and direction.
            limit: Maximum tracks to return.
            offset: Number of tracks to skip (ignored when keyset params present).
            after_value: Sort column value of the last row from the previous page.
            after_id: Primary key of the last row from the previous page.
            include_total: Whether to run the count query. False skips it and returns
                total=None (useful for cursor-paginated pages where the frontend
                already has the total from page 1).

        Returns:
            TrackListingPage with tracks, total, liked_track_ids, and next_page_key.
        """
        ...

    def find_tracks_by_title_artist(
        self, pairs: list[tuple[str, str]], *, user_id: str
    ) -> Awaitable[dict[tuple[str, str], Track]]:
        """Find existing tracks by (title, first_artist) pairs (case-insensitive).

        Args:
            pairs: List of (title, first_artist_name) tuples to search for.

        Returns:
            Dict keyed by lowercased (title, artist) → Track.
        """
        ...

    def find_tracks_by_isrcs(
        self, isrcs: list[str], *, user_id: str
    ) -> Awaitable[dict[str, Track]]:
        """Batch lookup tracks by ISRC.

        Args:
            isrcs: Normalized ISRC strings to search for.

        Returns:
            Dict keyed by ISRC → Track.
        """
        ...

    def find_tracks_by_mbids(
        self, mbids: list[str], *, user_id: str
    ) -> Awaitable[dict[str, Track]]:
        """Batch lookup tracks by MusicBrainz Recording ID.

        Args:
            mbids: MBID strings to search for.

        Returns:
            Dict keyed by MBID → Track.
        """
        ...

    def find_duplicate_tracks_by_fingerprint(
        self, *, user_id: str
    ) -> Awaitable[list[dict[str, object]]]:
        """Find tracks with identical (title, first_artist, album) tuples.

        Returns:
            List of dicts with title, artist, album, track_ids, count.
        """
        ...


class TrackIdentityServiceProtocol(Protocol):
    """Service interface for track identity resolution operations.

    This protocol defines the interface for resolving track identities across
    music services. It abstracts the implementation details of identity resolution
    to support Clean Architecture dependency inversion.
    """

    def get_raw_external_matches(
        self,
        tracks: list[Track],
        connector: str,
        connector_instance: object,
        progress_callback: ProgressCallback | None = None,
        **additional_options: object,
    ) -> Awaitable[dict[UUID, RawProviderMatch]]:
        """Get raw matches from external providers without business logic.

        Args:
            tracks: Tracks to get raw matches for (must have database IDs).
            connector: Target connector name.
            connector_instance: Connector implementation.
            progress_callback: Optional async callback invoked with
                (completed_count, total, description) after each matching phase.
            **additional_options: Options forwarded to providers.

        Returns:
            Track IDs mapped to raw provider match data.
        """
        ...

    def get_existing_identity_mappings(
        self, track_ids: list[UUID], connector: str
    ) -> Awaitable[MatchResultsById]:
        """Retrieve existing identity mappings from database.

        Args:
            track_ids: Track IDs to check for existing mappings.
            connector: Target connector name.

        Returns:
            Track IDs mapped to MatchResult objects for existing identity mappings.
        """
        ...

    def persist_identity_mappings(
        self, matches: MatchResultsById, connector: str
    ) -> Awaitable[None]:
        """Save identity mappings to database.

        Args:
            matches: Track IDs mapped to MatchResult objects.
            connector: Target connector name.
        """
        ...


class TrackMergeServiceProtocol(Protocol):
    """Service interface for track merging operations."""

    def merge_tracks(
        self, winner_id: UUID, loser_id: UUID, uow: UnitOfWorkProtocol
    ) -> Awaitable[Track]:
        """Merge two canonical tracks by moving references and soft-deleting loser.

        Args:
            winner_id: Track ID that will keep all references.
            loser_id: Track ID that will be soft-deleted.
            uow: Unit of work for transaction management.

        Returns:
            Winner track after merge.
        """
        ...
