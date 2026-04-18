"""Playlist metadata mapping domain entities.

A ``PlaylistMetadataMapping`` ties a cached connector playlist
(``DBConnectorPlaylist``) to a metadata action Mixd should apply to every
track in that playlist — either a preference state (``hmm`` / ``nah`` /
``yah`` / ``star``) or a tag. One connector playlist can carry multiple
mappings, so "Workout Starred" can map to both ``preference=star`` AND
``tag: context:workout``.

Mappings target the **connector** playlist, not the canonical Mixd
``Playlist``. A user can tag-map a Spotify playlist without forking it
into a canonical Mixd Playlist — tag application flows through the
existing ``ConnectorTrack → Track`` mapping.

``PlaylistMappingMember`` rows are a snapshot of which canonical tracks
were matched on the last import. They are **replaced** on each import
(DELETE + INSERT for that mapping_id), so membership diffs — "this track
was in the Starred playlist last time, now it's not; clear its mapping-
sourced preference" — are computable without accumulation errors.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid7

from attrs import define, field

from .shared import utc_now_factory
from .tag import normalize_tag

type MappingActionType = Literal["set_preference", "add_tag"]

_VALID_PREFERENCE_VALUES: frozenset[str] = frozenset(("hmm", "nah", "yah", "star"))


def validate_action_value(action_type: MappingActionType, raw: str) -> str:
    """Return the canonical ``action_value`` or raise ``ValueError``.

    For ``set_preference`` actions, the value must be a valid
    :data:`~src.domain.entities.preference.PreferenceState` literal.
    For ``add_tag`` actions, the value is normalized via
    :func:`normalize_tag`. Called from the domain ``create``/constructor,
    Pydantic API schemas, and CLI validators — single source of truth
    across all entry points.
    """
    match action_type:
        case "set_preference":
            if raw not in _VALID_PREFERENCE_VALUES:
                raise ValueError(
                    f"action_value for set_preference must be one of "
                    f"hmm/nah/yah/star, got {raw!r}"
                )
            return raw
        case "add_tag":
            return normalize_tag(raw)


@define(frozen=True, slots=True)
class PlaylistMetadataMapping:
    """One metadata action that will be applied to every track in a
    connector playlist on the next import.

    The constructor validates that ``action_value`` is already in its
    canonical form. Use :meth:`create` to build from raw user input —
    it normalizes (tag) or rejects (invalid preference) before
    construction.
    """

    user_id: str
    connector_playlist_id: UUID
    action_type: MappingActionType
    action_value: str
    id: UUID = field(factory=uuid7)

    def __attrs_post_init__(self) -> None:
        canonical = validate_action_value(self.action_type, self.action_value)
        if canonical != self.action_value:
            raise ValueError(
                f"action_value {self.action_value!r} is not canonical; "
                f"call PlaylistMetadataMapping.create() to normalize raw input"
            )

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
        """Build from a raw (possibly unnormalized) action_value.

        Normalizes tags (case-fold, trim); validates preference literals.
        Raises :class:`ValueError` on invalid input.
        """
        return cls(
            user_id=user_id,
            connector_playlist_id=connector_playlist_id,
            action_type=action_type,
            action_value=validate_action_value(action_type, raw_action_value),
            id=id if id is not None else uuid7(),
        )


@define(frozen=True, slots=True)
class PlaylistMappingMember:
    """One (mapping, track) pair from the last import's membership snapshot.

    ``synced_at`` is the timestamp of the import that produced this row,
    used by Epic 2's conflict-detection ("most recently imported wins"
    tiebreaker) and for diff-against-previous-snapshot removal tracking.

    ``user_id`` is denormalized from the parent mapping so RLS can enforce
    isolation on direct queries against this table.
    """

    user_id: str
    mapping_id: UUID
    track_id: UUID
    synced_at: datetime = field(factory=utc_now_factory)
    id: UUID = field(factory=uuid7)
