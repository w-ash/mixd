"""Artist repository protocols: canonical artists, connector records, favorites, aliases.

One module per aggregate, like ``track.py`` and ``like.py``. Four protocols
split by what they persist — the canonical row, the connector-side record and
its mappings, the favorite presence rows, and the alias cache — because each
has its own table and its own writer.

Two of the declared sorts order by something ``artists`` does not store:
``track_count`` is a count over ``track_artists`` and ``favorited_at`` lives on
``artist_favorites``. They are declared ``computed`` and the repository
resolves each to a correlated subquery; the keyset declaration is otherwise
identical, so paging, cursors and the NULL tail all behave as they do for a
stored column.
"""

from collections.abc import Awaitable, Mapping, Sequence
from datetime import datetime
from typing import Final, Literal, Protocol, TypedDict, TypeIs
from uuid import UUID

from attrs import define

from src.domain.entities.artist import (
    Artist,
    ArtistAlias,
    ArtistKind,
    ConnectorArtist,
)
from src.domain.entities.shared import JsonDict, SortKey
from src.domain.entities.track import Track
from src.domain.matching.artist_resolution import ArtistCreditSource
from src.domain.matching.config import MatchingConfig
from src.domain.repositories.keyset import KeysetSort, sorts
from src.domain.repositories.mapping import (
    ElectionMode,
    PrimaryCandidate,
)

type ArtistSortBy = Literal[
    "name_asc",
    "name_desc",
    "track_count_desc",
    "track_count_asc",
    "favorited_at_desc",
]

DEFAULT_ARTIST_SORT: Final[ArtistSortBy] = "name_asc"

# The one artist sort registry: the repository orders and seeks by it, the
# cursor codec encodes by it. ``sorts`` stamps each entry's wire key.
ARTIST_SORTS: Final[Mapping[ArtistSortBy, KeysetSort]] = sorts({
    "name_asc": KeysetSort("name", "asc"),
    "name_desc": KeysetSort("name", "desc"),
    # A count is never NULL, and an artist with no credits counts zero.
    "track_count_desc": KeysetSort("track_count", "desc", computed=True),
    "track_count_asc": KeysetSort("track_count", "asc", computed=True),
    # NULL for an artist the user has not favorited, so the tail is ordered.
    "favorited_at_desc": KeysetSort(
        "favorited_at", "desc", nullable=True, is_datetime=True, computed=True
    ),
})


def is_artist_sort(value: str) -> TypeIs[ArtistSortBy]:
    """Narrow a request-supplied sort key to a declared one."""
    return value in ARTIST_SORTS


class ArtistListingPage(TypedDict):
    """Result shape for paginated artist listing queries.

    The side maps are batched over the page's ids — one query each, never one
    per row — because the list page renders a track count, a favorite star and
    the service badges on every row.
    """

    artists: list[Artist]
    total: int | None  # None when the count was skipped (cursor pages)
    track_counts: dict[UUID, int]
    favorited_ids: set[UUID]
    connector_names: dict[UUID, list[str]]
    # The last row's (sort value, id); None on the last page.
    next_page_key: tuple[object, UUID] | None


class ArtistMappingInfo(TypedDict):
    """One live mapping plus the connector artist it names, for the detail page.

    ``name`` is the service's own spelling, not the canonical one, so the
    detail page can show how each service writes the artist. ``raw_metadata``
    rides along because this cycle has no ``artist_relations`` table:
    same-person and member-of links are read out of the connector payload by
    the page that shows them.
    """

    mapping_id: UUID
    connector_name: str
    connector_artist_identifier: str
    match_method: str
    confidence: int
    origin: str
    is_primary: bool
    name: str
    raw_metadata: JsonDict


class ArtistRepositoryProtocol(Protocol):
    """Repository interface for canonical artist persistence."""

    def get_artist_by_id(
        self, artist_id: UUID, *, user_id: str
    ) -> Awaitable[Artist | None]:
        """Get one artist, scoped to the user. None when absent or another tenant's."""
        ...

    def save_artists(self, artists: Sequence[Artist]) -> Awaitable[list[Artist]]:
        """Insert a batch of new canonical artists, returning them in input order.

        One multi-row INSERT. Ids are the entity's own — ``Artist.id`` is
        minted by the domain, so the caller already knows what it wrote.
        """
        ...

    def list_artists(
        self,
        *,
        user_id: str,
        query: str | None = None,
        favorites_only: bool = False,
        sort_by: ArtistSortBy = DEFAULT_ARTIST_SORT,
        limit: int = 50,
        offset: int = 0,
        after_value: SortKey | None = None,
        after_id: UUID | None = None,
        include_total: bool = True,
    ) -> Awaitable[ArtistListingPage]:
        """List artists with search, favorites filter, sorting and pagination.

        Keyset paging engages when ``after_id`` is given and falls back to
        OFFSET otherwise, exactly as the track listing does. ``query`` is a
        substring match on the artist name.
        """
        ...

    def list_needing_enrichment(
        self,
        *,
        user_id: str,
        older_than: datetime | None = None,
        limit: int = 100,
    ) -> Awaitable[list[Artist]]:
        """Artists with no MBID, or whose identity is older than ``older_than``.

        Ordered oldest-first (NULLS FIRST) so a trickle-scheduled enrichment
        pass always takes the least recently touched rows.
        """
        ...

    def set_identity(
        self,
        artist_id: UUID,
        *,
        user_id: str,
        mbid: str | None,
        kind: ArtistKind | None,
    ) -> Awaitable[None]:
        """Write the MusicBrainz anchor and entity kind, touching ``updated_at``."""
        ...

    def touch(self, artist_ids: Sequence[UUID], *, user_id: str) -> Awaitable[None]:
        """Move ``updated_at`` on a batch — the enrichment "seen, nothing found" mark."""
        ...

    def count_tracks_by_artist(
        self, artist_ids: Sequence[UUID], *, user_id: str
    ) -> Awaitable[dict[UUID, int]]:
        """Count distinct credited tracks per artist in one grouped query."""
        ...


class ArtistConnectorRepositoryProtocol(Protocol):
    """Connector-artist cache plus the mapping seam for artists."""

    def bulk_upsert_connector_artists(
        self, connector_name: str, artists: Sequence[ConnectorArtist]
    ) -> Awaitable[dict[str, ConnectorArtist]]:
        """Upsert connector artist records, keyed by connector identifier.

        A renamed artist is a *touch* of its row, never a new one: the service
        identifier is the identity, so ``name``, ``raw_metadata`` and
        ``last_updated`` are overwritten in place.

        For a caller holding the connector's own payload. A caller that only
        learned an identifier second-hand wants
        :meth:`ensure_connector_artists`.
        """
        ...

    def ensure_connector_artists(
        self, connector_name: str, artists: Sequence[ConnectorArtist]
    ) -> Awaitable[dict[str, ConnectorArtist]]:
        """Insert the connector artists that are absent, touch the ones that exist.

        Insert-or-touch: an existing row keeps its ``name`` and
        ``raw_metadata`` and only moves ``last_updated``, and the row returned
        is the stored one. ``connector_artists`` is a global cache every
        tenant reads, so a caller that inferred an identifier without seeing
        the service's own record — a MusicBrainz url-rel states an id and a
        URL, nothing more — must not overwrite what a first-hand payload
        wrote.
        """
        ...

    def find_connector_artists(
        self, connector_name: str, identifiers: Sequence[str]
    ) -> Awaitable[dict[str, ConnectorArtist]]:
        """The stored connector-artist rows for a connector's identifiers, keyed by identifier.

        Absent identifiers are simply missing from the result.
        """
        ...

    def find_artists_by_connector_artist_ids(
        self, connector_artist_ids: Sequence[UUID], *, user_id: str
    ) -> Awaitable[dict[UUID, Artist]]:
        """Canonical artists reachable from connector row ids through live mappings."""
        ...

    def get_full_mappings_for_artist(
        self, artist_id: UUID, *, user_id: str
    ) -> Awaitable[list[ArtistMappingInfo]]:
        """Every live mapping on an artist, joined to its connector record."""
        ...

    def touch_last_seen(
        self, connector: str, connector_artist_ids: Sequence[UUID], *, user_id: str
    ) -> Awaitable[None]:
        """Stamp ``last_seen_at`` on the mappings of re-encountered connector artists.

        Re-encounter is freshness, not evidence: confidence and primacy are
        never touched here, and manual overrides are stamped too.
        """
        ...

    def ensure_primaries(
        self, candidates: Sequence[PrimaryCandidate], *, mode: ElectionMode = "fill"
    ) -> Awaitable[list[PrimaryCandidate]]:
        """Elect one primary mapping per (artist, connector) pair."""
        ...

    def assert_mappings(
        self, rows: Sequence[Mapping[str, object]]
    ) -> Awaitable[object]:
        """Assert a batch of artist mappings, returning an opaque assertion.

        The assertion is the generic mapping mechanism's own value; a caller
        holds it only to hand it back to :meth:`record_assertion` once the
        primaries it names are elected.
        """
        ...

    def record_assertion(self, assertion: object) -> Awaitable[None]:
        """Emit the resolution events one assertion earned."""
        ...


@define(frozen=True, slots=True)
class ArtistMintSummary:
    """What one minting pass wrote, for the caller's log line.

    Connector-artist rows are not counted here: the connector-track writer
    stores them with the credits, before the minter runs.
    """

    artists_created: int = 0
    artists_reused: int = 0
    credits_assigned: int = 0

    @property
    def empty(self) -> bool:
        return self == ArtistMintSummary()


class ArtistMinterProtocol(Protocol):
    """The import-path artist minting walk, one home for both its callers.

    Every decision is the domain's (``domain.matching.artist_resolution``);
    the minter does the repository calls in the order ``ArtistWrites`` lists
    them, inside the caller's transaction, and never touches the network.
    """

    def mint(
        self,
        connector: str,
        sources: Sequence[ArtistCreditSource],
        canonicals: Mapping[str, Track],
        *,
        user_id: str,
        config: MatchingConfig,
    ) -> Awaitable[ArtistMintSummary]:
        """Read the connector records, probe owners, plan, and persist the writes.

        ``canonicals`` maps each source's key to the canonical track it
        resolved to, so the credits that named an artist can be filled on it.
        ``config`` prices every planner decision.
        """
        ...


class ArtistFavoriteRepositoryProtocol(Protocol):
    """Presence rows for the artists a user favorited.

    Mixd-only curation: the row exists while the artist is favorited and is
    deleted when it is not, so every method here reads or writes presence.
    """

    def favorite(self, artist_id: UUID, *, user_id: str) -> Awaitable[bool]:
        """Favorite an artist. True when the row was created, False when it existed."""
        ...

    def unfavorite(self, artist_id: UUID, *, user_id: str) -> Awaitable[bool]:
        """Unfavorite an artist. True when a row was deleted."""
        ...

    def get_favorite_status_batch(
        self, artist_ids: Sequence[UUID], *, user_id: str
    ) -> Awaitable[set[UUID]]:
        """Which of the given artists are favorited — one query for a page of rows."""
        ...

    def get_favorite_artist_ids(self, *, user_id: str) -> Awaitable[frozenset[UUID]]:
        """Every favorited artist id, for filters that need the whole set."""
        ...


class ArtistAliasRepositoryProtocol(Protocol):
    """The per-connector-artist alias cache.

    Aliases hang off the connector record, so nothing here is user-scoped —
    the table is a shared cache exactly like ``connector_artists``.
    """

    def replace_aliases(
        self, connector_artist_id: UUID, aliases: Sequence[ArtistAlias]
    ) -> Awaitable[int]:
        """Replace one connector artist's aliases wholesale, returning the row count.

        A refresh is a replacement, not a merge: MusicBrainz can retire an
        alias, and a merge would keep serving it forever.
        """
        ...

    def get_aliases_for_connector_artists(
        self, connector_artist_ids: Sequence[UUID]
    ) -> Awaitable[dict[UUID, list[ArtistAlias]]]:
        """Aliases for a batch of connector artists, keyed by connector artist id."""
        ...

    def find_connector_artist_ids_by_alias(
        self, names: Sequence[str]
    ) -> Awaitable[dict[str, list[UUID]]]:
        """Connector artists carrying any of these names as an alias.

        Case-insensitive on ``name`` and keyed by the caller's own spelling, so
        a lookup reads back under the string it asked with.
        """
        ...


__all__ = [
    "ARTIST_SORTS",
    "DEFAULT_ARTIST_SORT",
    "ArtistAliasRepositoryProtocol",
    "ArtistConnectorRepositoryProtocol",
    "ArtistFavoriteRepositoryProtocol",
    "ArtistListingPage",
    "ArtistMappingInfo",
    "ArtistMintSummary",
    "ArtistMinterProtocol",
    "ArtistRepositoryProtocol",
    "ArtistSortBy",
    "is_artist_sort",
]
