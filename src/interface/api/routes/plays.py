"""Play-history endpoints (v0.10.4): event feed + histogram.

One filter model serves both — the histogram is the feed's date navigator,
and ``track_id`` narrows either to a single track (Track Detail reuses this
instead of a per-track route).
"""

from datetime import datetime
from typing import Annotated, Final
from uuid import UUID
import zoneinfo

from fastapi import APIRouter, Depends, Query
from pydantic import AfterValidator

from src.application.runner import execute_use_case
from src.application.use_cases.get_plays_histogram import (
    GetPlaysHistogramCommand,
    GetPlaysHistogramUseCase,
)
from src.application.use_cases.list_plays import ListPlaysCommand, ListPlaysUseCase
from src.interface.api.deps import get_current_user_id, trigger_play_refresh
from src.interface.api.schemas.plays import (
    HistogramBinSchema,
    PlayEventSchema,
    PlayHistogramResponse,
    PlayListResponse,
)

# Reading plays is a demand signal for freshness, same as track detail.
router = APIRouter(
    prefix="/plays", tags=["plays"], dependencies=[Depends(trigger_play_refresh)]
)

# Resolved once — ``available_timezones()`` walks the tzdata tree.
_KNOWN_TIMEZONES: Final = frozenset(zoneinfo.available_timezones())


def _known_timezone(value: str) -> str:
    """Reject unknown zones here so Postgres never raises on ``timezone()``.

    ``tz`` reaches SQL as a bound parameter, so this is not an injection
    guard — it turns a stale browser zone into a 422 instead of the 500 an
    ``invalid_parameter_value`` from the database would surface as.
    """
    if value not in _KNOWN_TIMEZONES:
        raise ValueError(f"unknown IANA timezone: {value!r}")
    return value


TimezoneParam = Annotated[
    str,
    AfterValidator(_known_timezone),
    Query(description="IANA timezone for bin boundaries"),
]


@router.get("")
async def list_plays(
    user_id: Annotated[str, Depends(get_current_user_id)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(description="Opaque keyset cursor")] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    service: Annotated[
        str | None, Query(description="Filter to one source service")
    ] = None,
    track_id: Annotated[UUID | None, Query(description="Filter to one track")] = None,
) -> PlayListResponse:
    """List play events newest-first, keyset-paginated."""
    command = ListPlaysCommand(
        user_id=user_id,
        limit=limit,
        encoded_cursor=cursor,
        since=since,
        until=until,
        service=service,
        track_id=track_id,
    )
    result = await execute_use_case(
        lambda uow: ListPlaysUseCase().execute(command, uow),
        user_id=user_id,
    )
    return PlayListResponse(
        data=[PlayEventSchema.model_validate(p) for p in result.plays],
        limit=limit,
        next_cursor=result.next_cursor,
    )


@router.get("/histogram")
async def get_plays_histogram(
    user_id: Annotated[str, Depends(get_current_user_id)],
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    service: Annotated[str | None, Query()] = None,
    track_id: Annotated[UUID | None, Query()] = None,
    tz: TimezoneParam = "UTC",
) -> PlayHistogramResponse:
    """Binned play counts over time; bin size chosen server-side from the span."""
    command = GetPlaysHistogramCommand(
        user_id=user_id,
        since=since,
        until=until,
        service=service,
        track_id=track_id,
        tz=tz,
    )
    result = await execute_use_case(
        lambda uow: GetPlaysHistogramUseCase().execute(command, uow),
        user_id=user_id,
    )
    return PlayHistogramResponse(
        bins=[HistogramBinSchema.model_validate(b) for b in result.bins],
        bucket=result.bucket,
    )
