"""Use case for listing and searching tracks in the library.

Merged search+list: when `query` is provided it's a search, when None it's a
plain listing. This avoids two nearly-identical use cases and maps cleanly to
`GET /tracks?q=...`.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Literal
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
from src.domain.entities.preference import PreferenceState
from src.domain.repositories.interfaces import (
    TrackFacets,
    TrackListingPage,
    UnitOfWorkProtocol,
)

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class ListTracksCommand:
    """Parameters for listing/searching tracks."""

    user_id: str
    query: str | None = None
    liked: bool | None = None
    connector: str | None = None
    preference: str | None = None
    tags: Sequence[str] | None = None
    tag_mode: Literal["and", "or"] = "and"
    namespace: str | None = None
    sort_by: TrackSortBy = "title_asc"
    limit: int = field(default=BusinessLimits.DEFAULT_PAGE_SIZE)
    offset: int = 0
    cursor: str | None = None
    include_facets: bool = False


@define(frozen=True, slots=True)
class ListTracksResult:
    """Paginated track listing result."""

    tracks: list[Track]
    total: int | None
    limit: int
    offset: int
    liked_track_ids: set[UUID]
    preference_map: dict[UUID, PreferenceState]
    tag_map: dict[UUID, list[str]] = field(factory=dict)
    next_cursor: str | None = None
    facets: TrackFacets | None = None


@define(slots=True)
class ListTracksUseCase:
    """List and search tracks with server-side pagination, filtering, and sorting."""

    @staticmethod
    def _resolve_cursor(
        cursor: str, sort_column: str
    ) -> tuple[str | int | float | datetime | None, UUID | None, bool]:
        """Decode the cursor and resolve keyset bounds for the active sort.

        Holds the fallible decode/convert so the caller's ``try``/``except
        ValueError`` stays narrow while still covering both raising calls
        (``decode_cursor`` and ``cursor_sort_value_to_query``); an invalid
        cursor raises ``ValueError`` and the caller falls back to offset paging.
        Returns ``(after_value, after_id, has_cursor)``; a sort-column mismatch
        yields ``(None, None, False)``.
        """
        page_cursor = decode_cursor(cursor)
        if page_cursor.sort_column != sort_column:
            logger.debug(
                "Cursor sort column mismatch: "
                f"cursor={page_cursor.sort_column}, current={sort_column}"
            )
            return None, None, False
        after_value = cursor_sort_value_to_query(sort_column, page_cursor.sort_value)
        return after_value, page_cursor.last_id, True

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
                after_value, after_id, has_cursor = self._resolve_cursor(
                    command.cursor, sort_column
                )
            except ValueError:
                logger.debug("Invalid cursor, falling back to offset")

        async with uow:
            track_repo = uow.get_track_repository()
            page: TrackListingPage = await track_repo.list_tracks(
                user_id=command.user_id,
                query=command.query,
                liked=command.liked,
                connector=command.connector,
                preference=command.preference,
                tags=command.tags,
                tag_mode=command.tag_mode,
                namespace=command.namespace,
                sort_by=command.sort_by,
                limit=command.limit,
                offset=command.offset,
                after_value=after_value,
                after_id=after_id,
                include_total=not has_cursor,
                include_facets=command.include_facets,
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

            # Batch-fetch preferences and tags for this page of tracks
            track_ids = [t.id for t in page["tracks"]]
            prefs = await uow.get_preference_repository().get_preferences(
                track_ids, user_id=command.user_id
            )
            preference_map: dict[UUID, PreferenceState] = {
                tid: p.state for tid, p in prefs.items()
            }
            tags_by_track = await uow.get_tag_repository().get_tags(
                track_ids, user_id=command.user_id
            )
            tag_map: dict[UUID, list[str]] = {
                tid: [t.tag for t in tag_list]
                for tid, tag_list in tags_by_track.items()
            }

            return ListTracksResult(
                tracks=page["tracks"],
                total=page["total"],
                limit=command.limit,
                offset=command.offset,
                liked_track_ids=page["liked_track_ids"],
                preference_map=preference_map,
                tag_map=tag_map,
                next_cursor=next_cursor,
                facets=page["facets"],
            )
