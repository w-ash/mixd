"""Shared helpers for chat tool dispatchers.

Three concerns the domain dispatcher modules all reuse:

- **Argument coercion** — the input schemas carry no ``strict`` grammar and no
  numeric bounds (Anthropic rejects both for a broad registry), so the model can
  hand a dispatcher a missing field, a wrong type, or an out-of-range value.
  Every ``require_*``/``opt_*`` raises :class:`ToolExecutionError` with a
  corrective message the model self-fixes from within the same turn — the same
  actionable-error contract the workflow tools use.
- **Two-phase proposal** — :func:`propose_action` stores a pending action and
  returns the ``pending_confirmation`` payload the frontend keys on.
- **Entity projection** — compact, model-facing dicts built from domain
  entities (never interface Pydantic schemas — the application layer imports
  inward only). User-originated free text is wrapped in :class:`UserText` so the
  model boundary quotes it as data.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from uuid import UUID

from src.application.chat.pending_actions import pending_action_store
from src.application.chat.protocols import ToolContext
from src.application.chat.user_data import wrap
from src.application.runner import execute_use_case
from src.domain.entities.playlist import Playlist
from src.domain.entities.preference import PreferenceState
from src.domain.entities.shared import JsonDict, JsonValue
from src.domain.entities.track import Track
from src.domain.exceptions import NotFoundError, ToolExecutionError
from src.domain.repositories.uow import UnitOfWorkProtocol

# --- argument coercion ------------------------------------------------------


def require_str(args: Mapping[str, JsonValue], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolExecutionError(f"{key!r} is required and must be a non-empty string")
    return value


def opt_str(args: Mapping[str, JsonValue], key: str) -> str | None:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolExecutionError(f"{key!r} must be a string")
    return value


def require_uuid(args: Mapping[str, JsonValue], key: str) -> UUID:
    raw = args.get(key)
    try:
        return UUID(str(raw))
    except (ValueError, TypeError) as e:
        raise ToolExecutionError(f"{key!r} must be a UUID string, got {raw!r}") from e


def opt_uuid(args: Mapping[str, JsonValue], key: str) -> UUID | None:
    if args.get(key) is None:
        return None
    return require_uuid(args, key)


def require_choice(
    args: Mapping[str, JsonValue], key: str, choices: Sequence[str]
) -> str:
    value = args.get(key)
    if value not in choices:
        allowed = ", ".join(choices)
        raise ToolExecutionError(f"{key!r} must be one of: {allowed} (got {value!r})")
    return str(value)


def opt_choice(
    args: Mapping[str, JsonValue], key: str, choices: Sequence[str], default: str
) -> str:
    if args.get(key) is None:
        return default
    return require_choice(args, key, choices)


def opt_int(
    args: Mapping[str, JsonValue],
    key: str,
    *,
    default: int,
    minimum: int = 1,
    maximum: int = 500,
) -> int:
    raw = args.get(key)
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ToolExecutionError(f"{key!r} must be an integer")
    if raw < minimum or raw > maximum:
        raise ToolExecutionError(f"{key!r} must be between {minimum} and {maximum}")
    return raw


def opt_bool(args: Mapping[str, JsonValue], key: str, *, default: bool) -> bool:
    raw = args.get(key)
    if raw is None:
        return default
    if not isinstance(raw, bool):
        raise ToolExecutionError(f"{key!r} must be true or false")
    return raw


def require_uuid_list(args: Mapping[str, JsonValue], key: str) -> list[UUID]:
    raw = args.get(key)
    if not isinstance(raw, list) or not raw:
        raise ToolExecutionError(f"{key!r} must be a non-empty list of UUID strings")
    out: list[UUID] = []
    for item in raw:
        try:
            out.append(UUID(str(item)))
        except (ValueError, TypeError) as e:
            raise ToolExecutionError(
                f"Every item in {key!r} must be a UUID string, got {item!r}"
            ) from e
    return out


def require_str_list(args: Mapping[str, JsonValue], key: str) -> list[str]:
    raw = args.get(key)
    if not isinstance(raw, list) or not raw:
        raise ToolExecutionError(f"{key!r} must be a non-empty list of strings")
    if not all(isinstance(item, str) and item.strip() for item in raw):
        raise ToolExecutionError(f"Every item in {key!r} must be a non-empty string")
    return [str(item) for item in raw]


# --- two-phase proposal -----------------------------------------------------


async def propose_action(
    ctx: ToolContext,
    tool_name: str,
    tool_input: Mapping[str, JsonValue],
    description: str,
    details: JsonDict,
) -> JsonDict:
    """Store a pending action and return its ``pending_confirmation`` payload.

    Contract the frontend keys on: ``status``/``action_id``/``description``/
    ``details``. ``details`` keeps raw values — the confirmed executor reads them
    back to commit — plus, for write tools, a human-readable ``changes`` list and
    an optional destructive ``warning`` the ConfirmationCard renders.
    """
    action = await pending_action_store.create(
        user_id=ctx.user_id,
        tool_name=tool_name,
        tool_input=dict(tool_input),
        description=description,
        details=details,
    )
    return {
        "status": "pending_confirmation",
        "action_id": str(action.action_id),
        "description": description,
        "details": details,
    }


# --- commit envelope --------------------------------------------------------


async def commit[TResult](
    factory: Callable[[UnitOfWorkProtocol], Awaitable[TResult]],
    user_id: str,
    *,
    not_found: str,
    invalid_prefix: str,
) -> TResult:
    """Run a write use case, mapping commit-time failures to actionable errors.

    A target that vanished between propose and confirm (``NotFoundError``) or a
    command that no longer validates (``ValueError`` — e.g. a use-case guard or a
    definition a deploy tightened rules on) surfaces as a ``ToolExecutionError``
    the model can act on rather than a raw failure. Each call site supplies its
    own ``not_found`` sentence and ``invalid_prefix`` (the ``ValueError`` text is
    appended as ``f"{invalid_prefix}: {e}"``).
    """
    try:
        return await execute_use_case(factory, user_id=user_id)
    except NotFoundError as e:
        raise ToolExecutionError(not_found) from e
    except ValueError as e:
        raise ToolExecutionError(f"{invalid_prefix}: {e}") from e


# --- entity projection ------------------------------------------------------


def iso(value: datetime | None) -> str | None:
    """ISO-8601 string for a datetime, or ``None``."""
    return value.isoformat() if value is not None else None


def user_text(value: str | None) -> JsonValue:
    """Eager-wrap a user-originated display string in ``<user_data>`` tags.

    Attacker-controllable library text (track titles, playlist/tag names) reaches
    the model quoted as data; the SSE event boundary strips the tags so the
    frontend renders the raw value. ``None`` passes through. Only *display*
    fields get wrapped — never structured/id fields a write commits from.
    """
    return wrap(value) if value is not None else None


def project_track(
    track: Track,
    *,
    liked: bool | None = None,
    preference: PreferenceState | None = None,
    tags: Sequence[str] | None = None,
) -> JsonDict:
    """Compact model-facing view of a Track — ids raw, free text marked."""
    out: JsonDict = {
        "track_id": str(track.id),
        "title": user_text(track.title),
        "artists": [user_text(a.name) for a in track.artists],
    }
    if track.album:
        out["album"] = user_text(track.album)
    if track.isrc:
        out["isrc"] = track.isrc
    if liked is not None:
        out["liked"] = liked
    if preference is not None:
        out["preference"] = str(preference)
    if tags:
        out["tags"] = [user_text(t) for t in tags]
    return out


def project_playlist(playlist: Playlist) -> JsonDict:
    """Compact model-facing view of a Playlist (no entries)."""
    return {
        "playlist_id": str(playlist.id),
        "name": user_text(playlist.name),
        "description": user_text(playlist.description),
        "track_count": playlist.track_count,
        "updated_at": iso(playlist.updated_at),
    }
