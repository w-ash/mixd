"""Connector snapshot read tools — Discogs collection and Tidal favorites.

Thin adapters over the snapshot use cases: fetch the raw snapshot (count +
recent additions, unmatched — both make zero canonical writes) and project
it into a compact model-facing dict. Service-originated free text (titles,
artist credits) is wrapped in ``<user_data>`` tags via ``user_text`` so the
model boundary quotes it as data.
"""

from collections.abc import Mapping

from src.application.chat.dispatchers._common import opt_int, user_text
from src.application.chat.protocols import ToolContext
from src.application.use_cases.get_discogs_snapshot import run_get_discogs_snapshot
from src.application.use_cases.get_tidal_snapshot import run_get_tidal_snapshot
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
    result = await run_get_discogs_snapshot(ctx.user_id, recent_limit=recent_limit)
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


async def handle_get_tidal_snapshot(
    tool_input: Mapping[str, JsonValue],
    ctx: ToolContext,
) -> JsonValue:
    """Run the favorites snapshot use case and project the result for the model.

    An empty collection is a normal answer (total_items 0, recent []) — the
    zero-state invitation, not an error. A missing Tidal connection raises
    ``TidalAuthRequiredError``, which the loop surfaces with its reconnect
    remedy. ``recent_limit`` is capped low: each recent row costs one
    per-track Tidal lookup (the relationship serves identifiers only).
    """
    recent_limit = opt_int(tool_input, "recent_limit", default=10, maximum=25)
    result = await run_get_tidal_snapshot(ctx.user_id, recent_limit=recent_limit)
    recent: list[JsonValue] = [
        {
            "title": user_text(item.title),
            "artists": user_text(item.artists),
            "added_at": item.added_at,
        }
        for item in result.recent
    ]
    return {"total_items": result.total_items, "recent": recent}


GET_TIDAL_SNAPSHOT_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "recent_limit": {
            "type": "integer",
            "description": (
                "How many recently favorited tracks to return (default 10, "
                "max 25 — each costs one per-track lookup)."
            ),
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
    {
        "name": "get_tidal_snapshot",
        "description": (
            "Call this to see the user's Tidal favorites as Tidal reports "
            "them: the total number of favorited tracks and the most "
            "recently added ones (title, artists, date favorited). Raw and "
            "unmatched — nothing here is linked to library tracks yet. A "
            "total of 0 means no favorites, which is a normal answer, not a "
            "failure. Use it for questions about what the user has "
            "favorited on Tidal or how large their Tidal collection is."
        ),
        "input_schema": GET_TIDAL_SNAPSHOT_INPUT_SCHEMA,
        "dispatch": handle_get_tidal_snapshot,
        "use_cases": ("GetTidalSnapshotUseCase",),
        "kind": "read",
    },
]
