"""``get_discogs_snapshot`` — the read tool over the Discogs collection.

A thin adapter over ``GetDiscogsSnapshotUseCase``: fetch the raw collection
snapshot (count + recent additions, unmatched — v0.11.1 makes zero canonical
writes) and project it into a compact model-facing dict. Discogs-originated
free text (titles, artist credits) is wrapped in ``<user_data>`` tags via
``user_text`` so the model boundary quotes it as data.
"""

from collections.abc import Mapping

from src.application.chat.dispatchers._common import opt_int, user_text
from src.application.chat.protocols import ToolContext
from src.application.runner import execute_use_case
from src.application.use_cases.get_discogs_snapshot import (
    GetDiscogsSnapshotCommand,
    GetDiscogsSnapshotUseCase,
)
from src.domain.entities.shared import JsonDict, JsonValue


async def handle_get_discogs_snapshot(
    tool_input: Mapping[str, JsonValue],
    ctx: ToolContext,
) -> JsonValue:
    """Run the snapshot use case and project the result for the model.

    An empty collection is a normal answer (total_items 0, recent []) — the
    zero-state invitation, not an error. A missing Discogs connection raises
    ``DiscogsAuthRequiredError``, which the loop surfaces with its reconnect
    remedy.
    """
    recent_limit = opt_int(tool_input, "recent_limit", default=10, maximum=50)
    command = GetDiscogsSnapshotCommand(user_id=ctx.user_id, recent_limit=recent_limit)
    result = await execute_use_case(
        lambda uow: GetDiscogsSnapshotUseCase().execute(command, uow),
        user_id=ctx.user_id,
    )
    recent: list[JsonValue] = [
        {
            "title": user_text(item.title),
            "artists": user_text(item.artists),
            "year": item.year,
            "formats": item.formats,
            "date_added": item.date_added,
        }
        for item in result.recent
    ]
    return {
        "username": result.username,
        "total_items": result.total_items,
        "recent": recent,
    }


GET_DISCOGS_SNAPSHOT_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "recent_limit": {
            "type": "integer",
            "description": "How many recently added items to return (default 10).",
        },
    },
    "additionalProperties": False,
}


SPECS: list[dict[str, object]] = [
    {
        "name": "get_discogs_snapshot",
        "description": (
            "Call this to see the user's Discogs record collection as Discogs "
            "reports it: the total number of collected releases and the most "
            "recently added items (title, artist credits, year, physical "
            "formats, date added). Raw and unmatched — nothing here is linked "
            "to library tracks yet. A total of 0 means the collection is "
            "empty, which is a normal answer, not a failure. Use it for "
            "questions about what the user owns on vinyl/CD or how large "
            "their Discogs collection is."
        ),
        "input_schema": GET_DISCOGS_SNAPSHOT_INPUT_SCHEMA,
        "dispatch": handle_get_discogs_snapshot,
        "use_cases": ("GetDiscogsSnapshotUseCase",),
        "kind": "read",
    },
]
