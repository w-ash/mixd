"""Track tag domain entities.

Tags categorize tracks by what they ARE — mood, energy, context, vibe
(``mood:chill``, ``energy:high``, ``banger``). Tags follow the shared
patterns established by preferences (v0.7.0): ``MetadataSource`` /
``should_override`` for source priority, an append-only event log, and
caller-provided ``tagged_at`` source timestamps.

Unlike preferences (one per track), tags are many-per-track. The unique
key is three-part ``(user_id, track_id, tag)``.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Final, Literal
from uuid import UUID, uuid7

from attrs import Factory, define, field

from .sourced_metadata import MetadataSource

type TagAction = Literal["add", "remove"]

MAX_TAG_LENGTH: Final[int] = 64

_VALID_CHARS: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9 _\-:]+$")
_WHITESPACE_RUN: Final[re.Pattern[str]] = re.compile(r"\s+")


def normalize_tag(raw: str) -> str:
    """Return the canonical form of a raw tag string.

    Raises:
        ValueError: If the tag is empty, too long, contains characters
            outside ``[a-z0-9 _-:]``, or starts / ends with ``:``.
    """
    normalized = _WHITESPACE_RUN.sub(" ", raw.strip().lower())
    if not normalized:
        raise ValueError("tag must not be empty")
    if len(normalized) > MAX_TAG_LENGTH:
        raise ValueError(
            f"tag must be {MAX_TAG_LENGTH} characters or fewer, got {len(normalized)}"
        )
    if not _VALID_CHARS.match(normalized):
        raise ValueError(
            f"tag contains invalid characters (allowed: a-z 0-9 space _ - :): {normalized!r}"
        )
    if normalized.startswith(":") or normalized.endswith(":"):
        raise ValueError("tag must not start or end with ':'")
    return normalized


def parse_tag(normalized: str) -> tuple[str | None, str]:
    """Split a normalized tag into ``(namespace, value)`` on the first colon.

    ``"mood:chill"`` → ``("mood", "chill")``; ``"banger"`` → ``(None, "banger")``;
    ``"mood:chill:vibes"`` → ``("mood", "chill:vibes")`` — only the first colon
    splits, so nested colons stay in the value.
    """
    namespace, sep, value = normalized.partition(":")
    if not sep:
        return None, normalized
    return namespace, value


def _derive_namespace(self: TrackTag) -> str | None:
    return parse_tag(self.tag)[0]


def _derive_value(self: TrackTag) -> str:
    return parse_tag(self.tag)[1]


@define(frozen=True, slots=True)
class TrackTag:
    """A tag on a (user, track) pair.

    ``tag`` must already be normalized — use :meth:`TrackTag.create` to build
    from a raw string. ``namespace`` and ``value`` are derived from ``tag``
    at construction so they cannot diverge.
    """

    user_id: str
    track_id: UUID
    tag: str
    tagged_at: datetime
    source: MetadataSource
    namespace: str | None = field(
        init=False,
        default=Factory(_derive_namespace, takes_self=True),
    )
    value: str = field(
        init=False,
        default=Factory(_derive_value, takes_self=True),
    )
    id: UUID = field(factory=uuid7)

    @classmethod
    def create(
        cls,
        *,
        user_id: str,
        track_id: UUID,
        raw_tag: str,
        tagged_at: datetime,
        source: MetadataSource,
        id: UUID | None = None,
    ) -> TrackTag:
        """Normalize ``raw_tag`` and build a fully-derived ``TrackTag``.

        Raises:
            ValueError: If ``raw_tag`` fails normalization.
        """
        extra: dict[str, UUID] = {"id": id} if id is not None else {}
        return cls(
            user_id=user_id,
            track_id=track_id,
            tag=normalize_tag(raw_tag),
            tagged_at=tagged_at,
            source=source,
            **extra,
        )


@define(frozen=True, slots=True)
class TagEvent:
    """Append-only record of a tag add or remove.

    Events are never updated or deleted so the full timeline is recoverable
    even after a tag is removed and re-added.
    """

    user_id: str
    track_id: UUID
    tag: str
    action: TagAction
    source: MetadataSource
    tagged_at: datetime
    id: UUID = field(factory=uuid7)
