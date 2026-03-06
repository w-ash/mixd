"""Use case for listing and searching tracks in the library.

Merged search+list: when `query` is provided it's a search, when None it's a
plain listing. This avoids two nearly-identical use cases and maps cleanly to
`GET /tracks?q=...`.
"""

from attrs import define, field

from src.config.constants import BusinessLimits
from src.domain.entities import Track
from src.domain.repositories.interfaces import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class ListTracksCommand:
    """Parameters for listing/searching tracks."""

    query: str | None = None
    liked: bool | None = None
    connector: str | None = None
    sort_by: str = "title_asc"
    limit: int = field(default=BusinessLimits.DEFAULT_PAGE_SIZE)
    offset: int = 0


@define(frozen=True, slots=True)
class ListTracksResult:
    """Paginated track listing result."""

    tracks: list[Track]
    total: int
    limit: int
    offset: int
    liked_track_ids: set[int]


@define(slots=True)
class ListTracksUseCase:
    """List and search tracks with server-side pagination, filtering, and sorting."""

    async def execute(
        self, command: ListTracksCommand, uow: UnitOfWorkProtocol
    ) -> ListTracksResult:
        """Execute the track listing operation.

        Args:
            command: Search/filter/sort/pagination parameters.
            uow: Unit of work for repository access.

        Returns:
            ListTracksResult with tracks, pagination metadata, and liked IDs.
        """
        async with uow:
            track_repo = uow.get_track_repository()
            tracks, total, liked_track_ids = await track_repo.list_tracks(
                query=command.query,
                liked=command.liked,
                connector=command.connector,
                sort_by=command.sort_by,
                limit=command.limit,
                offset=command.offset,
            )
            return ListTracksResult(
                tracks=tracks,
                total=total,
                limit=command.limit,
                offset=command.offset,
                liked_track_ids=liked_track_ids,
            )
