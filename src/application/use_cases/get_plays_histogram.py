"""Plays-over-time histogram (v0.10.4) — feeds the chart date navigator."""

from datetime import UTC, datetime, timedelta
from typing import Final, Literal
from uuid import UUID

from attrs import define, field

from src.application.use_cases._shared.command_validators import as_utc
from src.domain.repositories.uow import UnitOfWorkProtocol

type HistogramBucket = Literal["day", "week", "month"]

# Span → bin size (starting point, revisit against real chart density).
_DAY_SPAN_MAX: Final = timedelta(days=90)
_WEEK_SPAN_MAX: Final = timedelta(days=730)


@define(frozen=True, slots=True)
class GetPlaysHistogramCommand:
    user_id: str
    since: datetime | None = field(default=None, converter=as_utc)
    until: datetime | None = field(default=None, converter=as_utc)
    service: str | None = None
    track_id: UUID | None = None
    tz: str = "UTC"


@define(frozen=True, slots=True)
class HistogramBin:
    bucket_start: datetime
    count: int


@define(frozen=True, slots=True)
class GetPlaysHistogramResult:
    bins: list[HistogramBin]
    bucket: HistogramBucket


def _bucket_for_span(span: timedelta) -> HistogramBucket:
    if span <= _DAY_SPAN_MAX:
        return "day"
    if span <= _WEEK_SPAN_MAX:
        return "week"
    return "month"


@define(slots=True)
class GetPlaysHistogramUseCase:
    async def execute(
        self,
        command: GetPlaysHistogramCommand,
        uow: UnitOfWorkProtocol,
    ) -> GetPlaysHistogramResult:
        until = command.until or datetime.now(UTC)

        async with uow:
            plays_repo = uow.get_plays_repository()

            if command.since is not None:
                bucket = _bucket_for_span(until - command.since)
                bins = await plays_repo.get_play_histogram(
                    user_id=command.user_id,
                    bucket=bucket,
                    since=command.since,
                    until=until,
                    service=command.service,
                    track_id=command.track_id,
                    tz=command.tz,
                )
            else:
                # Open range: bin monthly first, then rebin when the observed
                # span turns out narrow. At most two indexed aggregate queries.
                bins = await plays_repo.get_play_histogram(
                    user_id=command.user_id,
                    bucket="month",
                    until=until,
                    service=command.service,
                    track_id=command.track_id,
                    tz=command.tz,
                )
                bucket = "month"
                if bins:
                    observed = _bucket_for_span(until - bins[0][0].replace(tzinfo=UTC))
                    if observed != "month":
                        bucket = observed
                        bins = await plays_repo.get_play_histogram(
                            user_id=command.user_id,
                            bucket=bucket,
                            until=until,
                            service=command.service,
                            track_id=command.track_id,
                            tz=command.tz,
                        )

        return GetPlaysHistogramResult(
            bins=[HistogramBin(bucket_start=b, count=c) for b, c in bins],
            bucket=bucket,
        )
