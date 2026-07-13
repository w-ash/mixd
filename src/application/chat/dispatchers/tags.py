"""Read tool: list the user's tags with usage counts.

``list_tags`` is a thin adapter over ``ListTagsUseCase`` so the assistant can
enumerate the user's real tags (with per-tag track counts and last-used
timestamps) before filtering, suggesting, or answering questions about them —
never guessing tag names. Tag strings are user-authored free text, so each is
returned wrapped in a ``UserText`` marker.
"""

from collections.abc import Mapping

from src.application.chat.dispatchers._common import opt_int, opt_str, user_text
from src.application.chat.protocols import ToolContext
from src.application.runner import execute_use_case
from src.application.use_cases.list_tags import ListTagsCommand, ListTagsUseCase
from src.domain.entities.shared import JsonDict, JsonValue

LIST_TAGS_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Optional substring to match tag names (e.g. 'mood'); omit to "
                "list the most-used tags."
            ),
        },
        "limit": {
            "type": "integer",
            "description": "Max tags to return (1-500, default 100).",
        },
    },
    "additionalProperties": False,
}


async def handle_list_tags(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    """List the user's tags, ordered by usage count."""
    command = ListTagsCommand(
        user_id=ctx.user_id,
        query=opt_str(tool_input, "query"),
        limit=opt_int(tool_input, "limit", default=100),
    )
    result = await execute_use_case(
        lambda uow: ListTagsUseCase().execute(command, uow),
        user_id=ctx.user_id,
    )
    tags = [
        {
            "tag": user_text(tag),
            "track_count": count,
            "last_used_at": last.isoformat() if last else None,
        }
        for tag, count, last in result.tags
    ]
    return {"tags": tags}


SPECS: list[dict[str, object]] = [
    {
        "name": "list_tags",
        "description": (
            "Call this to see the user's tags with how many tracks carry each "
            "and when each was last used. Use it before filtering the library "
            "by tag or answering questions about tags, so tag names are real "
            "rather than guessed."
        ),
        "input_schema": LIST_TAGS_INPUT_SCHEMA,
        "dispatch": handle_list_tags,
        "use_cases": ("ListTagsUseCase",),
        "kind": "read",
        # Hot set: tag/preference basics — loaded upfront rather than searched.
        "defer_loading": False,
    },
]
