"""Write tool: ``favorite_artist`` — two-phase-confirmed artist favorites.

Propose/commit, like the other ``*_write`` dispatchers: the handler validates
the artist id and the target state and stores them on a pending action; nothing
is written until the user confirms, at which point ``exec_favorite_artist``
runs the same ``FavoriteArtistUseCase`` the web UI and CLI call.

Favoriting is a presence row, so a confirmed repeat is a no-op rather than an
error — the result's ``changed`` flag is what lets the assistant say "already
favorited" instead of claiming a write it did not make.
"""

from collections.abc import Mapping

from src.application.chat.dispatchers._common import (
    commit,
    confirmed,
    opt_bool,
    propose_action,
    require_uuid,
)
from src.application.chat.pending_actions import PendingAction
from src.application.chat.protocols import ToolContext
from src.application.use_cases.favorite_artist import (
    FavoriteArtistCommand,
    FavoriteArtistUseCase,
)
from src.domain.entities.shared import JsonDict, JsonValue

_COMMIT_NOT_FOUND = (
    "The change could not be applied — the artist no longer exists. It may have "
    "been removed since the change was proposed."
)
_COMMIT_INVALID_PREFIX = "The artist favorite change failed validation at confirm time"


FAVORITE_ARTIST_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "artist_id": {
            "type": "string",
            "description": (
                "UUID of the artist (from query_library with entity 'artists')."
            ),
        },
        "is_favorited": {
            "type": "boolean",
            "description": (
                "True to favorite (the default), false to remove the favorite."
            ),
        },
    },
    "required": ["artist_id"],
    "additionalProperties": False,
}


async def handle_favorite_artist(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    """Propose an artist favorite change — nothing persists until confirmed."""
    artist_id = require_uuid(tool_input, "artist_id")
    is_favorited = opt_bool(tool_input, "is_favorited", default=True)
    verb = "Favorite" if is_favorited else "Unfavorite"
    details: JsonDict = {
        "artist_id": str(artist_id),
        "is_favorited": is_favorited,
        "changes": [f"{verb} artist {artist_id}"],
    }
    return await propose_action(
        ctx, "favorite_artist", tool_input, f"{verb} 1 artist", details
    )


async def exec_favorite_artist(action: PendingAction, user_id: str) -> JsonValue:
    """Commit the proposed favorite change via its use case."""
    details = action.details
    command = FavoriteArtistCommand(
        user_id=user_id,
        artist_id=require_uuid(details, "artist_id"),
        is_favorited=bool(details.get("is_favorited", True)),
    )
    result = await commit(
        lambda uow: FavoriteArtistUseCase().execute(command, uow),
        user_id,
        not_found=_COMMIT_NOT_FOUND,
        invalid_prefix=_COMMIT_INVALID_PREFIX,
    )
    return confirmed(
        action,
        "favorite_artist",
        artist_id=str(result.artist_id),
        is_favorited=result.is_favorited,
        changed=result.changed,
    )


SPECS: list[dict[str, object]] = [
    {
        "name": "favorite_artist",
        "description": (
            "Call this to propose favoriting (or unfavoriting) one of the "
            "user's artists. Look up the real artist_id first with "
            "query_library entity='artists'; never guess it. Favorites are "
            "Mixd-only curation and never sync to a connected service."
        ),
        "input_schema": FAVORITE_ARTIST_INPUT_SCHEMA,
        "dispatch": handle_favorite_artist,
        "use_cases": ("FavoriteArtistUseCase",),
        "kind": "write",
        "executor": exec_favorite_artist,
    },
]
