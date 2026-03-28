"""Use case for listing and searching tracks in the library.

Merged search+list: when `query` is provided it's a search, when None it's a
plain listing. This avoids two nearly-identical use cases and maps cleanly to
`GET /tracks?q=...`.
"""

from uuid import UUID

from attrs import define, field

from src.application.pagination import (
    TRACK_SORT_COLUMNS,
    PageCursor,
    TrackSortBy,
    cursor_sort_value_from_row,
    cursor_sort_value_to_query,
    decode_cursor,
    encode_cursor,
)
from src.config import get_logger
from src.config.constants import BusinessLimits
from src.domain.entities import Track
from src.domain.repositories.interfaces import TrackListingPage, UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class ListTracksCommand:
    """Parameters for listing/searching tracks."""

    query: str | None = None
    liked: bool | None = None
    connector: str | None = None
    sort_by: TrackSortBy = "title_asc"
    limit: int = field(default=BusinessLimits.DEFAULT_PAGE_SIZE)
    offset: int = 0
    cursor: str | None = None


@define(frozen=True, slots=True)
class ListTracksResult:
    """Paginated track listing result."""

    tracks: list[Track]
    total: int | None
    limit: int
    offset: int
    liked_track_ids: set[UUID]
    next_cursor: str | None = None


@define(slots=True)
class ListTracksUseCase:
    """List and search tracks with server-side pagination, filtering, and sorting."""

    async def execute(
        self, command: ListTracksCommand, uow: UnitOfWorkProtocol
    ) -> ListTracksResult:
        """Execute the track listing operation.

        Decodes an optional cursor for keyset pagination, delegates to the
        repository, and encodes the next-page cursor for the response.
        """
        # Decode cursor if present and valid for the current sort
        after_value = None
        after_id = None
        sort_column = TRACK_SORT_COLUMNS[command.sort_by][0]
        has_cursor = False

        if command.cursor:
            try:
                page_cursor = decode_cursor(command.cursor)
                if page_cursor.sort_column == sort_column:
                    after_value = cursor_sort_value_to_query(
                        sort_column, page_cursor.sort_value
                    )
                    after_id = page_cursor.last_id
                    has_cursor = True
                else:
                    logger.debug(
                        "Cursor sort column mismatch: "
                        f"cursor={page_cursor.sort_column}, current={sort_column}"
                    )
            except ValueError:
                logger.debug("Invalid cursor, falling back to offset")

        async with uow:
            track_repo = uow.get_track_repository()
            page: TrackListingPage = await track_repo.list_tracks(
                query=command.query,
                liked=command.liked,
                connector=command.connector,
                sort_by=command.sort_by,
                limit=command.limit,
                offset=command.offset,
                after_value=after_value,
                after_id=after_id,
                include_total=not has_cursor,
            )

            # Encode next-page cursor from repository's raw key
            next_cursor: str | None = None
            if page["next_page_key"] is not None:
                raw_value, last_id = page["next_page_key"]
                next_cursor = encode_cursor(
                    PageCursor(
                        sort_column=sort_column,
                        sort_value=cursor_sort_value_from_row(sort_column, raw_value),
                        last_id=last_id,
                    )
                )

            return ListTracksResult(
                tracks=page["tracks"],
                total=page["total"],
                limit=command.limit,
                offset=command.offset,
                liked_track_ids=page["liked_track_ids"],
                next_cursor=next_cursor,
            )
