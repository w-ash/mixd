"""Read tool: query the user's raw play events and their over-time histogram."""

from collections.abc import Mapping
from datetime import datetime

from src.application.chat.dispatchers._common import (
    iso,
    opt_bool,
    opt_int,
    opt_str,
    opt_uuid,
    user_text,
)
from src.application.chat.protocols import ToolContext
from src.application.runner import execute_use_case
from src.application.use_cases.get_plays_histogram import (
    GetPlaysHistogramCommand,
    GetPlaysHistogramUseCase,
)
from src.application.use_cases.list_plays import ListPlaysCommand, ListPlaysUseCase
from src.domain.entities.shared import JsonDict, JsonValue
from src.domain.exceptions import ToolExecutionError

QUERY_PLAYS_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "track_id": {
            "type": "string",
            "description": "Restrict to one track's play events (UUID).",
        },
        "service": {
            "type": "string",
            "description": "Restrict to one source service ('spotify', 'lastfm').",
        },
        "since": {
            "type": "string",
            "description": "ISO-8601 lower bound on played_at (inclusive).",
        },
        "until": {
            "type": "string",
            "description": "ISO-8601 upper bound on played_at (exclusive).",
        },
        "limit": {
            "type": "integer",
            "description": "Events per page (default 50, max 200).",
        },
        "cursor": {
            "type": "string",
            "description": "Opaque next_cursor from a previous page.",
        },
        "histogram": {
            "type": "boolean",
            "description": (
                "When true, return binned play counts over time instead of "
                "individual events (bin size chosen from the range)."
            ),
        },
    },
    "additionalProperties": False,
}


def _opt_datetime(tool_input: Mapping[str, JsonValue], key: str) -> datetime | None:
    raw = opt_str(tool_input, key)
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ToolExecutionError(f"{key!r} must be an ISO-8601 datetime") from exc


async def handle_query_plays(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    since = _opt_datetime(tool_input, "since")
    until = _opt_datetime(tool_input, "until")
    service = opt_str(tool_input, "service")
    track_id = opt_uuid(tool_input, "track_id")

    if opt_bool(tool_input, "histogram", default=False):
        command = GetPlaysHistogramCommand(
            user_id=ctx.user_id,
            since=since,
            until=until,
            service=service,
            track_id=track_id,
        )
        result = await execute_use_case(
            lambda uow: GetPlaysHistogramUseCase().execute(command, uow),
            user_id=ctx.user_id,
        )
        return {
            "bucket": result.bucket,
            "bins": [
                {"bucket_start": iso(b.bucket_start), "count": b.count}
                for b in result.bins
            ],
        }

    list_command = ListPlaysCommand(
        user_id=ctx.user_id,
        limit=opt_int(tool_input, "limit", default=50, maximum=200),
        encoded_cursor=opt_str(tool_input, "cursor"),
        since=since,
        until=until,
        service=service,
        track_id=track_id,
    )
    list_result = await execute_use_case(
        lambda uow: ListPlaysUseCase().execute(list_command, uow),
        user_id=ctx.user_id,
    )
    return {
        "plays": [
            {
                "track_id": str(p.track_id),
                "title": user_text(p.title),
                "artists": user_text(p.artists),
                "played_at": iso(p.played_at),
                "ms_played": p.ms_played,
                "service": p.service,
                "source_services": list(p.source_services),
            }
            for p in list_result.plays
        ],
        "next_cursor": list_result.next_cursor,
    }


SPECS: list[dict[str, object]] = [
    {
        "name": "query_plays",
        "description": (
            "Call this to read the user's raw play history: individual play "
            "events (when each track was played, from which service), "
            "filterable by track, service, and date range, newest first — or "
            "set histogram=true for binned plays-over-time counts. Use "
            "query_library scope 'played' instead when you want tracks ranked "
            "by play stats rather than the events themselves."
        ),
        "input_schema": QUERY_PLAYS_INPUT_SCHEMA,
        "dispatch": handle_query_plays,
        "use_cases": ("ListPlaysUseCase", "GetPlaysHistogramUseCase"),
        "kind": "read",
    },
]
