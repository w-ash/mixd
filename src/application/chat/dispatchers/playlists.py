"""``query_playlists`` — the read tool over the playlist query use cases.

A thin adapter: it maps the model's scope (canonical listing, one canonical
playlist's detail + a tracks page, or a connector's playlists with import
status) onto the existing application query use cases and projects their domain
results into compact, model-facing dicts. No business logic lives here — where a
use case is missing or awkward, it is fixed at the source.
"""

from collections.abc import Mapping

from src.application.chat.dispatchers._common import (
    iso,
    opt_bool,
    opt_choice,
    opt_int,
    opt_str,
    project_playlist,
    project_track,
    require_str,
    user_text,
)
from src.application.chat.protocols import ToolContext
from src.application.runner import execute_use_case
from src.application.use_cases.list_connector_playlists import (
    ConnectorPlaylistView,
    ListConnectorPlaylistsCommand,
    ListConnectorPlaylistsUseCase,
)
from src.application.use_cases.list_playlists import (
    ListPlaylistsCommand,
    ListPlaylistsUseCase,
)
from src.application.use_cases.read_canonical_playlist import (
    ReadCanonicalPlaylistCommand,
    ReadCanonicalPlaylistUseCase,
    ReadPlaylistTracksPageCommand,
    ReadPlaylistTracksPageUseCase,
)
from src.domain.entities.playlist import PlaylistEntry
from src.domain.entities.shared import JsonDict, JsonValue

QUERY_PLAYLISTS_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "source": {
            "type": "string",
            "enum": ["canonical", "connector"],
            "description": (
                "Which library to query. 'canonical' (default) reads mixd's own "
                "unified playlists; 'connector' lists the playlists that live on "
                "an external service and whether each is imported yet."
            ),
        },
        "playlist_id": {
            "type": "string",
            "description": (
                "Canonical playlists only: a playlist id to fetch one playlist's "
                "detail plus a page of its tracks. Omit to list every canonical "
                "playlist. Ignored when source is 'connector'."
            ),
        },
        "connector": {
            "type": "string",
            "description": (
                "Connector name such as 'spotify'. Required when source is "
                "'connector'; optional for a canonical detail lookup by an "
                "external service id."
            ),
        },
        "force_refresh": {
            "type": "boolean",
            "description": (
                "Connector source only: re-fetch from the service instead of the "
                "cache. Defaults to false (cache-first)."
            ),
        },
        "limit": {
            "type": "integer",
            "description": (
                "Canonical detail only: page size for the tracks slice "
                "(1-500, default 50)."
            ),
        },
        "offset": {
            "type": "integer",
            "description": (
                "Canonical detail only: zero-based offset into the tracks list "
                "(default 0)."
            ),
        },
    },
    "additionalProperties": False,
}


def _project_connector_view(view: ConnectorPlaylistView) -> JsonDict:
    """Compact model-facing view of a connector playlist with import status."""
    return {
        "connector_playlist_id": view.connector_playlist_identifier,
        "connector_playlist_db_id": str(view.connector_playlist_db_id),
        "name": user_text(view.name),
        "description": user_text(view.description),
        "owner": user_text(view.owner),
        "track_count": view.track_count,
        "import_status": view.import_status,
    }


def _project_entry(entry: PlaylistEntry, position: int) -> JsonDict:
    """One playlist membership: position, resolved track (or None), added_at."""
    return {
        "position": position,
        "track": project_track(entry.track) if entry.track is not None else None,
        "added_at": iso(entry.added_at),
    }


async def _query_connector_playlists(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    connector = require_str(tool_input, "connector")
    command = ListConnectorPlaylistsCommand(
        user_id=ctx.user_id,
        connector_name=connector,
        force_refresh=opt_bool(tool_input, "force_refresh", default=False),
    )
    result = await execute_use_case(
        lambda uow: ListConnectorPlaylistsUseCase().execute(command, uow),
        user_id=ctx.user_id,
    )
    return {
        "source": "connector",
        "connector": connector,
        "from_cache": result.from_cache,
        "playlists": [_project_connector_view(v) for v in result.playlists],
    }


async def _list_canonical_playlists(ctx: ToolContext) -> JsonValue:
    command = ListPlaylistsCommand(user_id=ctx.user_id)
    result = await execute_use_case(
        lambda uow: ListPlaylistsUseCase().execute(command, uow),
        user_id=ctx.user_id,
    )
    return {
        "source": "canonical",
        "playlists": [project_playlist(p) for p in result.playlists],
        "total_count": result.total_count,
    }


async def _read_canonical_detail(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext, playlist_id: str
) -> JsonValue:
    connector = opt_str(tool_input, "connector")
    detail_command = ReadCanonicalPlaylistCommand(
        user_id=ctx.user_id,
        playlist_id=playlist_id,
        connector=connector,
    )
    detail = await execute_use_case(
        lambda uow: ReadCanonicalPlaylistUseCase().execute(detail_command, uow),
        user_id=ctx.user_id,
    )
    # ReadCanonicalPlaylist does not raise on miss — it returns a None playlist.
    if detail.playlist is None:
        return {
            "source": "canonical",
            "playlist": None,
            "message": (
                f"No canonical playlist matches {playlist_id!r}. Call "
                "query_playlists without playlist_id to list the real playlists "
                "and their ids."
            ),
        }

    page_command = ReadPlaylistTracksPageCommand(
        user_id=ctx.user_id,
        playlist_id=playlist_id,
        limit=opt_int(tool_input, "limit", default=50),
        offset=opt_int(tool_input, "offset", default=0, minimum=0),
        connector=connector,
    )
    page = await execute_use_case(
        lambda uow: ReadPlaylistTracksPageUseCase().execute(page_command, uow),
        user_id=ctx.user_id,
    )
    tracks = [
        _project_entry(entry, page.offset + index)
        for index, entry in enumerate(page.entries)
    ]
    return {
        "source": "canonical",
        "playlist": project_playlist(detail.playlist),
        "tracks": tracks,
        "total": page.total,
        "limit": page.limit,
        "offset": page.offset,
    }


async def handle_query_playlists(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    """Query playlists — a canonical listing/detail or a connector's playlists.

    Scopes: source='canonical' with no playlist_id lists every canonical
    playlist; with a playlist_id it returns that playlist's detail plus a page
    of its tracks (limit/offset). source='connector' requires a connector and
    lists that service's playlists with per-playlist import status.
    """
    source = opt_choice(tool_input, "source", ("canonical", "connector"), "canonical")

    if source == "connector":
        return await _query_connector_playlists(tool_input, ctx)

    playlist_id = opt_str(tool_input, "playlist_id")
    if playlist_id is None:
        return await _list_canonical_playlists(ctx)
    return await _read_canonical_detail(tool_input, ctx, playlist_id)


SPECS: list[dict[str, object]] = [
    {
        "name": "query_playlists",
        "description": (
            "Call this to read the user's playlists before answering questions "
            "about them, referencing one, or proposing changes — so names and "
            "ids are real, never guessed. source='canonical' lists mixd's "
            "unified playlists, or pass a playlist_id for one playlist's detail "
            "and a page of its tracks (limit/offset). source='connector' with a "
            "connector name lists that service's playlists and whether each is "
            "imported yet."
        ),
        "input_schema": QUERY_PLAYLISTS_INPUT_SCHEMA,
        "dispatch": handle_query_playlists,
        "use_cases": (
            "ListPlaylistsUseCase",
            "ReadCanonicalPlaylistUseCase",
            "ReadPlaylistTracksPageUseCase",
            "ListConnectorPlaylistsUseCase",
        ),
        "kind": "read",
    },
]
