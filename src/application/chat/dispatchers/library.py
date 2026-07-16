"""Read tool: query the user's track library.

``query_library`` is the assistant's single window onto a user's tracks. It
fans a ``scope`` discriminator over the existing library query paths — the
paginated listing/search (``ListTracksUseCase``), the assembled per-track
detail view (``GetTrackDetailsUseCase``), and the liked / preferred / played
source queries — so the model has one tool to learn instead of five. No
business logic lives here: every branch coerces the model's arguments, calls a
use case through ``execute_use_case``, and projects the domain result into a
compact, user-data-marked dict.
"""

from collections.abc import Mapping
from uuid import UUID

from src.application.chat.dispatchers._common import (
    iso,
    opt_choice,
    opt_int,
    opt_str,
    opt_uuid,
    project_track,
    require_choice,
    require_str_list,
    user_text,
)
from src.application.chat.protocols import ToolContext
from src.application.runner import execute_use_case
from src.application.use_cases.get_liked_tracks import (
    GetLikedTracksCommand,
    GetLikedTracksUseCase,
)
from src.application.use_cases.get_played_tracks import (
    GetPlayedTracksCommand,
    GetPlayedTracksUseCase,
)
from src.application.use_cases.get_preferred_tracks import (
    GetPreferredTracksCommand,
    GetPreferredTracksUseCase,
)
from src.application.use_cases.get_track_details import (
    GetTrackDetailsCommand,
    GetTrackDetailsUseCase,
)
from src.application.use_cases.list_tracks import ListTracksCommand, ListTracksUseCase
from src.domain.entities.preference import PreferenceState
from src.domain.entities.shared import JsonDict, JsonValue
from src.domain.exceptions import NotFoundError, ToolExecutionError

_SCOPES: tuple[str, ...] = ("all", "liked", "preferred", "played")
_PREFERENCE_STATES: tuple[str, ...] = ("hmm", "nah", "yah", "star")

QUERY_LIBRARY_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "scope": {
            "type": "string",
            "enum": list(_SCOPES),
            "description": (
                "Which slice of the library to read (default 'all'). 'all' "
                "lists/searches every track and accepts 'query', 'connector', "
                "'preference', 'tags', 'cursor', and 'track_id'. 'liked' returns "
                "liked tracks and accepts 'connector'. 'preferred' returns tracks "
                "with one preference state and REQUIRES 'state'. 'played' returns "
                "recently played tracks and accepts 'connector' and 'days_back'."
            ),
        },
        "track_id": {
            "type": "string",
            "description": (
                "With scope 'all', a track UUID to fetch the full detail view "
                "(play stats, playlist memberships, connector mappings, tags) "
                "instead of a listing. Ignored for other scopes."
            ),
        },
        "query": {
            "type": "string",
            "description": "Scope 'all' only: free-text search over title/artist/album.",
        },
        "connector": {
            "type": "string",
            "description": (
                "Filter to one service ('spotify', 'lastfm'). Used by scopes "
                "'all', 'liked', and 'played'."
            ),
        },
        "preference": {
            "type": "string",
            "enum": list(_PREFERENCE_STATES),
            "description": (
                "Scope 'all' only: keep tracks with this preference state "
                "(hmm/nah/yah/star)."
            ),
        },
        "state": {
            "type": "string",
            "enum": list(_PREFERENCE_STATES),
            "description": (
                "Scope 'preferred' only, REQUIRED: the preference state to "
                "return (hmm/nah/yah/star)."
            ),
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Scope 'all' only: keep only tracks carrying every listed tag.",
        },
        "days_back": {
            "type": "integer",
            "description": (
                "Scope 'played' only: restrict to plays within the last N days "
                "(1 or greater). Omit for all-time."
            ),
        },
        "limit": {
            "type": "integer",
            "description": "Max tracks to return (1-500, default 50).",
        },
        "cursor": {
            "type": "string",
            "description": (
                "Scope 'all' only: the 'next_cursor' from a previous call, to "
                "page forward."
            ),
        },
    },
    "additionalProperties": False,
}


def _require_preference_state(
    args: Mapping[str, JsonValue], key: str
) -> PreferenceState:
    """Coerce a required argument to a ``PreferenceState`` literal.

    ``require_choice`` validates membership; the ``match`` narrows the validated
    string to the literal union so no cast is needed.
    """
    value = require_choice(args, key, _PREFERENCE_STATES)
    match value:
        case "hmm" | "nah" | "yah" | "star":
            return value
        case _:  # unreachable — require_choice already rejected other values
            raise ToolExecutionError(f"{key!r} must be one of: hmm, nah, yah, star")


def _optional_preference(args: Mapping[str, JsonValue], key: str) -> str | None:
    if args.get(key) is None:
        return None
    return require_choice(args, key, _PREFERENCE_STATES)


def _optional_tags(args: Mapping[str, JsonValue], key: str) -> list[str] | None:
    if args.get(key) is None:
        return None
    return require_str_list(args, key)


def _optional_days_back(args: Mapping[str, JsonValue], key: str) -> int | None:
    if args.get(key) is None:
        return None
    return opt_int(args, key, default=1, minimum=1, maximum=3650)


async def handle_query_library(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    """Query the library, dispatching on ``scope`` to the matching use case."""
    scope = opt_choice(tool_input, "scope", _SCOPES, "all")
    limit = opt_int(tool_input, "limit", default=50)

    if scope == "all":
        track_id = opt_uuid(tool_input, "track_id")
        if track_id is not None:
            return await _track_detail(track_id, ctx)
        return await _list_tracks(tool_input, ctx, limit)
    if scope == "liked":
        return await _liked_tracks(tool_input, ctx, limit)
    if scope == "preferred":
        return await _preferred_tracks(tool_input, ctx, limit)
    return await _played_tracks(tool_input, ctx, limit)


async def _list_tracks(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext, limit: int
) -> JsonValue:
    command = ListTracksCommand(
        user_id=ctx.user_id,
        query=opt_str(tool_input, "query"),
        connector=opt_str(tool_input, "connector"),
        preference=_optional_preference(tool_input, "preference"),
        tags=_optional_tags(tool_input, "tags"),
        limit=limit,
        cursor=opt_str(tool_input, "cursor"),
    )
    result = await execute_use_case(
        lambda uow: ListTracksUseCase().execute(command, uow),
        user_id=ctx.user_id,
    )
    tracks = [
        project_track(
            track,
            liked=track.id in result.liked_track_ids,
            preference=result.preference_map.get(track.id),
            tags=result.tag_map.get(track.id),
        )
        for track in result.tracks
    ]
    return {"tracks": tracks, "total": result.total, "next_cursor": result.next_cursor}


async def _track_detail(track_id: UUID, ctx: ToolContext) -> JsonValue:
    command = GetTrackDetailsCommand(user_id=ctx.user_id, track_id=track_id)
    try:
        result = await execute_use_case(
            lambda uow: GetTrackDetailsUseCase().execute(command, uow),
            user_id=ctx.user_id,
        )
    except NotFoundError as e:
        raise ToolExecutionError(
            f"No track with id {track_id} — call query_library with scope 'all' "
            "to find real track ids."
        ) from e

    detail = project_track(result.track, preference=result.preference, tags=result.tags)
    detail["play_summary"] = {
        "total_plays": result.play_summary.total_plays,
        "first_played": iso(result.play_summary.first_played),
        "last_played": iso(result.play_summary.last_played),
    }
    detail["playlists"] = [
        {"playlist_id": str(p.id), "name": user_text(p.name)} for p in result.playlists
    ]
    detail["connectors"] = [
        {
            "connector": m.connector_name,
            "is_primary": m.is_primary,
            "title": user_text(m.connector_track_title),
            "artists": [user_text(a) for a in m.connector_track_artists],
        }
        for m in result.connector_mappings
    ]
    return detail


async def _liked_tracks(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext, limit: int
) -> JsonValue:
    command = GetLikedTracksCommand(
        user_id=ctx.user_id,
        limit=limit,
        connector_filter=opt_str(tool_input, "connector"),
    )
    result = await execute_use_case(
        lambda uow: GetLikedTracksUseCase().execute(command, uow),
        user_id=ctx.user_id,
    )
    tracks = [project_track(t, liked=True) for t in result.tracklist.tracks]
    return {"tracks": tracks, "total": result.total_available}


async def _preferred_tracks(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext, limit: int
) -> JsonValue:
    state = _require_preference_state(tool_input, "state")
    command = GetPreferredTracksCommand(user_id=ctx.user_id, state=state, limit=limit)
    result = await execute_use_case(
        lambda uow: GetPreferredTracksUseCase().execute(command, uow),
        user_id=ctx.user_id,
    )
    tracks = [project_track(t, preference=state) for t in result.tracklist.tracks]
    return {"tracks": tracks, "count": len(tracks)}


async def _played_tracks(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext, limit: int
) -> JsonValue:
    command = GetPlayedTracksCommand(
        user_id=ctx.user_id,
        limit=limit,
        days_back=_optional_days_back(tool_input, "days_back"),
        connector_filter=opt_str(tool_input, "connector"),
    )
    result = await execute_use_case(
        lambda uow: GetPlayedTracksUseCase().execute(command, uow),
        user_id=ctx.user_id,
    )
    tracks = [project_track(t) for t in result.tracklist.tracks]
    return {"tracks": tracks, "total": result.total_available}


SPECS: list[dict[str, object]] = [
    {
        "name": "query_library",
        "description": (
            "Call this to read the user's track library: search or list tracks "
            "(scope 'all'), inspect one track's full detail (scope 'all' with "
            "track_id), or pull the liked, preferred, or recently played slices "
            "(scope 'liked'/'preferred'/'played'). Use it whenever you need real "
            "track ids, titles, or counts instead of guessing."
        ),
        "input_schema": QUERY_LIBRARY_INPUT_SCHEMA,
        "dispatch": handle_query_library,
        "use_cases": (
            "ListTracksUseCase",
            "GetTrackDetailsUseCase",
            # The scope='all' + track_id detail view surfaces a track's playlist
            # memberships (via GetTrackDetails), covering this capability too.
            "GetTrackPlaylistsUseCase",
            "GetLikedTracksUseCase",
            "GetPreferredTracksUseCase",
            "GetPlayedTracksUseCase",
        ),
        "kind": "read",
        # Hot set: library search is the most common read — loaded upfront rather
        # than discovered via tool_search.
        "defer_loading": False,
    },
]
