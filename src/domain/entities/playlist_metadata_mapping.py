"""Playlist metadata mapping domain entities.

A mapping binds a cached connector playlist to a metadata action
(``set_preference`` or ``add_tag``). One ConnectorPlaylist can carry
multiple mappings.

Mappings target the cached connector playlist directly so users can
tag-map without forking into a canonical Mixd Playlist.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final, Literal, cast
from uuid import UUID, uuid7

from attrs import define, field

from .preference import PreferenceState
from .shared import utc_now_factory
from .tag import normalize_tag

type MappingActionType = Literal["set_preference", "add_tag"]

# Typing the element as the literal alias forces pyright to reject any
# typo'd value here at type-check time — keeping the runtime frozenset
# in sync with the type alias without runtime ``get_args`` introspection
# (which trips the strict reportAny rule on PEP 695 ``__value__``).
MAPPING_ACTION_TYPES: Final[frozenset[MappingActionType]] = frozenset({
    "set_preference",
    "add_tag",
})

_VALID_PREFERENCE_VALUES: Final[frozenset[PreferenceState]] = frozenset({
    "hmm",
    "nah",
    "yah",
    "star",
})


def validate_action_value(action_type: MappingActionType, raw: str) -> str:
    """Return the canonical ``action_value`` or raise ``ValueError``.

    Tags get normalized; preferences must already be a valid literal.
    Single source of truth for API schema, CLI helper, and the domain
    constructor.
    """
    match action_type:
        case "set_preference":
            if raw not in _VALID_PREFERENCE_VALUES:
                raise ValueError(
                    f"action_value for set_preference must be one of "
                    f"{', '.join(sorted(_VALID_PREFERENCE_VALUES))}, got {raw!r}"
                )
            return raw
        case "add_tag":
            return normalize_tag(raw)


@define(frozen=True, slots=True)
class PlaylistMetadataMapping:
    """One metadata action applied to every track in a connector playlist."""

    user_id: str
    connector_playlist_id: UUID
    action_type: MappingActionType
    action_value: str
    id: UUID = field(factory=uuid7)

    def __attrs_post_init__(self) -> None:
        canonical = validate_action_value(self.action_type, self.action_value)
        if canonical != self.action_value:
            object.__setattr__(self, "action_value", canonical)

    @classmethod
    def create(
        cls,
        *,
        user_id: str,
        connector_playlist_id: UUID,
        action_type: MappingActionType,
        raw_action_value: str,
        id: UUID | None = None,
    ) -> PlaylistMetadataMapping:
        """Build from a raw action value. Equivalent to direct construction
        but reads more clearly when the input is known to be unnormalized."""
        return cls(
            user_id=user_id,
            connector_playlist_id=connector_playlist_id,
            action_type=action_type,
            action_value=raw_action_value,
            id=id if id is not None else uuid7(),
        )

    def as_preference_state(self) -> PreferenceState:
        """Return ``action_value`` as a typed ``PreferenceState``.

        Caller must have already checked ``action_type == "set_preference"``;
        the value is provably a valid literal because ``validate_action_value``
        ran at construction.
        """
        if self.action_type != "set_preference":
            raise ValueError(
                f"as_preference_state() called on action_type={self.action_type!r}"
            )
        return cast(PreferenceState, self.action_value)

    def as_tag(self) -> str:
        """Return ``action_value`` as a normalized tag string."""
        if self.action_type != "add_tag":
            raise ValueError(f"as_tag() called on action_type={self.action_type!r}")
        return self.action_value


@define(frozen=True, slots=True)
class PlaylistMappingMember:
    """One (mapping, track) pair from the last import's membership snapshot.

    ``user_id`` is denormalized from the parent mapping for direct-query
    RLS isolation.
    """

    user_id: str
    mapping_id: UUID
    track_id: UUID
    synced_at: datetime = field(factory=utc_now_factory)
    id: UUID = field(factory=uuid7)
