"""Paginated play-event listing (v0.10.4) — the first raw-plays read surface."""

from datetime import datetime
from uuid import UUID

from attrs import define, field

from src.application.pagination import (
    PageCursor,
    cursor_sort_value_from_row,
    decode_cursor,
    encode_cursor,
)
from src.application.use_cases._shared.command_validators import as_utc
from src.domain.repositories.uow import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class ListPlaysCommand:
    user_id: str
    limit: int = 50
    encoded_cursor: str | None = None
    since: datetime | None = field(default=None, converter=as_utc)
    until: datetime | None = field(default=None, converter=as_utc)
    service: str | None = None
    track_id: UUID | None = None


@define(frozen=True, slots=True)
class PlayEventRow:
    """One play event joined with its track's display metadata."""

    id: UUID
    track_id: UUID
    title: str
    artists: str
    played_at: datetime
    ms_played: int | None
    service: str
    source_services: list[str]


@define(frozen=True, slots=True)
class ListPlaysResult:
    plays: list[PlayEventRow]
    next_cursor: str | None


@define(slots=True)
class ListPlaysUseCase:
    async def execute(
        self,
        command: ListPlaysCommand,
        uow: UnitOfWorkProtocol,
    ) -> ListPlaysResult:
        before: tuple[datetime, UUID] | None = None
        if command.encoded_cursor is not None:
            decoded = decode_cursor(command.encoded_cursor)
            # Cursor stores played_at as ISO string; parse here so the repo
            # never sees the wire format (precedent: list_operation_runs).
            if decoded.sort_value is not None:
                before = (
                    datetime.fromisoformat(str(decoded.sort_value)),
                    decoded.last_id,
                )

        async with uow:
            plays_repo = uow.get_plays_repository()
            plays, next_page_key = await plays_repo.list_play_events(
                user_id=command.user_id,
                before=before,
                since=command.since,
                until=command.until,
                service=command.service,
                track_id=command.track_id,
                limit=command.limit,
            )
            track_ids = list({p.track_id for p in plays if p.track_id is not None})
            tracks_by_id = await uow.get_track_repository().find_tracks_by_ids(
                track_ids
            )

        rows: list[PlayEventRow] = []
        for play in plays:
            if play.track_id is None:
                continue
            track = tracks_by_id.get(play.track_id)
            rows.append(
                PlayEventRow(
                    id=play.id,
                    track_id=play.track_id,
                    title=track.title if track else "Unknown track",
                    artists=track.artists_display if track else "",
                    played_at=play.played_at,
                    ms_played=play.ms_played,
                    service=play.service,
                    source_services=list(play.source_services or []),
                )
            )

        next_cursor: str | None = None
        if next_page_key is not None:
            next_played_at, next_id = next_page_key
            next_cursor = encode_cursor(
                PageCursor(
                    sort_column="played_at",
                    sort_value=cursor_sort_value_from_row("played_at", next_played_at),
                    last_id=next_id,
                )
            )

        return ListPlaysResult(plays=rows, next_cursor=next_cursor)
