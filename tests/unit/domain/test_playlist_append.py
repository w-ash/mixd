"""Unit tests for the pure append-mode dedup decision (select_appendable_entries).

Pins the workflow-append semantics: appending a track already present is a no-op,
id-less/unresolved positions are always kept (never collide on a track id), and
source order is preserved.
"""

from uuid import uuid7

from src.domain.entities.playlist import ConnectorTrackRef, PlaylistEntry
from src.domain.playlist import select_appendable_entries
from tests.fixtures import make_track


def _resolved(track_id=None) -> PlaylistEntry:
    return PlaylistEntry(track=make_track(id=track_id or uuid7()))


def _unresolved(identifier: str = "local1") -> PlaylistEntry:
    return PlaylistEntry(
        track=None,
        connector_track_ref=ConnectorTrackRef("spotify", identifier, title="Ghost"),
    )


class TestSelectAppendableEntries:
    """The dedup decision table for workflow append."""

    def test_empty_candidates_returns_empty(self) -> None:
        assert select_appendable_entries([_resolved()], []) == []

    def test_all_new_tracks_are_kept(self) -> None:
        current = [_resolved(), _resolved()]
        candidates = [_resolved(), _resolved()]
        assert select_appendable_entries(current, candidates) == candidates

    def test_existing_track_id_is_dropped(self) -> None:
        shared = uuid7()
        current = [_resolved(shared)]
        keep = _resolved()
        candidates = [_resolved(shared), keep]
        # Only the genuinely-new entry survives; the duplicate id is a no-op.
        assert select_appendable_entries(current, candidates) == [keep]

    def test_all_duplicates_returns_empty(self) -> None:
        tid1, tid2 = uuid7(), uuid7()
        current = [_resolved(tid1), _resolved(tid2)]
        candidates = [_resolved(tid1), _resolved(tid2)]
        assert select_appendable_entries(current, candidates) == []

    def test_unresolved_entries_are_always_kept(self) -> None:
        # Unresolved positions carry no canonical track id, so they can't collide
        # and are never deduped — even against an existing unresolved position.
        current = [_unresolved("local1")]
        candidates = [_unresolved("local1"), _unresolved("local2")]
        assert select_appendable_entries(current, candidates) == candidates

    def test_order_is_preserved(self) -> None:
        tid_dupe = uuid7()
        current = [_resolved(tid_dupe)]
        first, second, third = _resolved(), _resolved(), _resolved()
        candidates = [first, _resolved(tid_dupe), second, third]
        # The duplicate is removed; the remaining new entries keep their order.
        assert select_appendable_entries(current, candidates) == [first, second, third]
