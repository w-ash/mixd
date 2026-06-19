"""Track-tag repository protocol.

Split from the former monolithic ``interfaces.py``.
"""

from collections.abc import Awaitable, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from src.domain.entities.sourced_metadata import MetadataSource
from src.domain.entities.tag import TagEvent, TrackTag


class TagRepositoryProtocol(Protocol):
    """Repository interface for track tag persistence.

    Batch-first: single-item operations pass a one-element sequence. The
    UNIQUE key is three-part ``(user_id, track_id, tag)`` (unlike
    preferences' two-part key), because a track can carry many tags.
    ``add_tags`` uses ON CONFLICT DO NOTHING at the DB layer and returns
    only the rows actually inserted, so callers can build event rows for
    real changes only.
    """

    def get_tags(
        self, track_ids: Sequence[UUID], *, user_id: str
    ) -> Awaitable[dict[UUID, list[TrackTag]]]:
        """Get tags for a set of tracks. Returns {track_id: [tags]}."""
        ...

    def add_tags(
        self, tags: Sequence[TrackTag], *, user_id: str
    ) -> Awaitable[list[TrackTag]]:
        """Bulk insert tags with ON CONFLICT DO NOTHING.

        Returns only the tags actually inserted — duplicates are silently
        skipped. Callers should write one ``TagEvent`` per returned row.
        """
        ...

    def remove_tags(
        self,
        pairs: Sequence[tuple[UUID, str]],
        *,
        user_id: str,
        source: MetadataSource | None = None,
    ) -> Awaitable[list[tuple[UUID, str]]]:
        """Remove (track_id, tag) pairs. Returns the pairs actually removed.

        Missing rows are silently skipped (idempotent). Callers should
        write one ``TagEvent`` per returned pair.

        When ``source`` is provided, only tags matching that source are
        removed — used by the playlist-metadata-mapping flow to clear
        only its own contributions without touching manual tags.
        """
        ...

    def add_events(
        self, events: Sequence[TagEvent], *, user_id: str
    ) -> Awaitable[list[TagEvent]]:
        """Append tag add/remove events. Events are never updated."""
        ...

    def list_tags(
        self,
        *,
        user_id: str,
        query: str | None = None,
        limit: int = 100,
    ) -> Awaitable[list[tuple[str, int, datetime]]]:
        """List tags with track counts and last-used timestamp, sorted by count desc.

        When ``query`` is set, results are filtered via the trigram index
        (GIN on ``tag``) for autocomplete. Returns
        ``[(tag, track_count, last_used_at)]`` where ``last_used_at`` is the
        most recent ``tagged_at`` across all rows for that tag.
        Track-side filtering by tag (for the Library page) flows through
        ``TrackRepositoryProtocol.list_tracks`` so pagination, sort, and
        hydration happen in one query.
        """
        ...

    def rename_tag(self, *, user_id: str, source: str, target: str) -> Awaitable[int]:
        """Rename ``source`` tag to ``target`` across all of one user's tracks.

        Idempotent on tracks that already carry ``target`` — those just
        lose the ``source`` row (no duplicate target inserted). Tracks
        without the conflict get a new ``target`` row that preserves the
        source row's ``tagged_at`` and ``source`` so provenance is
        retained.

        Writes per-track ``remove(source)`` events for every affected
        track, plus ``add(target)`` events only for tracks that didn't
        already carry ``target`` — keeping the audit log accurate about
        actual state changes.

        Returns the number of tracks that previously carried ``source``
        (i.e., the affected-track count).
        """
        ...

    def delete_tag(self, *, user_id: str, tag: str) -> Awaitable[int]:
        """Bulk-delete ``tag`` from all of one user's tracks.

        Cascades to the event log: rows in ``track_tag_events`` for the
        deleted tag are also removed (the audit trail's subject no longer
        exists). No remove events are written. Returns the number of
        ``track_tags`` rows deleted.
        """
        ...

    def merge_tags(self, *, user_id: str, source: str, target: str) -> Awaitable[int]:
        """Merge ``source`` into ``target``. Same operation as ``rename_tag`` —
        exposed under a different name for API expressiveness when
        ``target`` is known to already exist on some tracks.
        """
        ...
