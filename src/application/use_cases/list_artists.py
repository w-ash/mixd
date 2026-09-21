"""Use case for listing and searching artists in the library.

Merged search+list, like ``list_tracks.py``: ``search`` present is a search,
absent is a plain listing, so one use case backs ``GET /artists?search=…`` and
the artists list page's unfiltered first load.

The side maps (track counts, favorited ids, connector names) come back batched
from the repository rather than being re-read per row — the list page renders
all three on every row.
"""

from uuid import UUID

from attrs import define, field

from src.application.pagination import (
    PageCursor,
    cursor_sort_value_from_row,
    encode_cursor,
    resolve_cursor,
)
from src.config import get_logger
from src.config.constants import BusinessLimits
from src.domain.entities.artist import Artist
from src.domain.repositories.artist import (
    ARTIST_SORTS,
    DEFAULT_ARTIST_SORT,
    ArtistListingPage,
    ArtistSortBy,
    is_artist_sort,
)
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)


def _known_sort(value: str) -> ArtistSortBy:
    """Unknown keys take the default sort so programmatic callers never 500.

    The API's ``ArtistSortBy`` Query type rejects garbage with a 422 before it
    reaches here; the repository takes an ``ArtistSortBy``.
    """
    return value if is_artist_sort(value) else DEFAULT_ARTIST_SORT


@define(frozen=True, slots=True)
class ListArtistsCommand:
    """Parameters for listing/searching artists."""

    user_id: str
    cursor: str | None = None
    limit: int = field(default=BusinessLimits.DEFAULT_PAGE_SIZE)
    offset: int = 0
    search: str | None = None
    sort_by: ArtistSortBy = field(default=DEFAULT_ARTIST_SORT, converter=_known_sort)
    favorites_only: bool = False


@define(frozen=True, slots=True)
class ListArtistsResult:
    """Paginated artist listing result plus the per-row side maps."""

    artists: list[Artist]
    total: int | None
    limit: int
    offset: int
    next_cursor: str | None = None
    track_counts: dict[UUID, int] = field(factory=dict)
    favorited_ids: set[UUID] = field(factory=set)
    connector_names: dict[UUID, list[str]] = field(factory=dict)


@define(slots=True)
class ListArtistsUseCase:
    """List and search artists with server-side pagination, filtering, sorting."""

    async def execute(
        self, command: ListArtistsCommand, uow: UnitOfWorkProtocol
    ) -> ListArtistsResult:
        """Execute the artist listing operation."""
        after_value = None
        after_id = None
        sort = ARTIST_SORTS[command.sort_by]
        has_cursor = False

        if command.cursor:
            try:
                after_value, after_id, has_cursor = resolve_cursor(command.cursor, sort)
            except ValueError:
                logger.debug("Invalid cursor, falling back to offset")

        async with uow:
            page: ArtistListingPage = await uow.get_artist_repository().list_artists(
                user_id=command.user_id,
                query=command.search,
                favorites_only=command.favorites_only,
                sort_by=command.sort_by,
                limit=command.limit,
                offset=command.offset,
                after_value=after_value,
                after_id=after_id,
                include_total=not has_cursor,
            )

            next_cursor: str | None = None
            if page["next_page_key"] is not None:
                raw_value, last_id = page["next_page_key"]
                next_cursor = encode_cursor(
                    PageCursor(
                        sort_key=sort.key,
                        sort_value=cursor_sort_value_from_row(raw_value),
                        last_id=last_id,
                    )
                )

            return ListArtistsResult(
                artists=page["artists"],
                total=page["total"],
                limit=command.limit,
                offset=command.offset,
                next_cursor=next_cursor,
                track_counts=page["track_counts"],
                favorited_ids=page["favorited_ids"],
                connector_names=page["connector_names"],
            )


async def run_list_artists(
    *,
    user_id: str,
    search: str | None = None,
    favorites_only: bool = False,
    sort_by: ArtistSortBy = DEFAULT_ARTIST_SORT,
    limit: int = 50,
    cursor: str | None = None,
) -> ListArtistsResult:
    """List artists via ``execute_use_case`` — the CLI's entry point."""
    from src.application.runner import execute_use_case

    command = ListArtistsCommand(
        user_id=user_id,
        search=search,
        favorites_only=favorites_only,
        sort_by=sort_by,
        limit=limit,
        cursor=cursor,
    )
    return await execute_use_case(
        lambda uow: ListArtistsUseCase().execute(command, uow),
        user_id=user_id,
    )
