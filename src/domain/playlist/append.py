"""Pure append-mode membership logic for canonical playlists.

Sits beside the diff engine because it is the same kind of pure membership
mechanics — entities only, no I/O. ``select_appendable_entries`` owns the
dedup-vs-append decision that keeps a workflow's weekly re-run idempotent on
track id while never dropping distinct unresolved source positions.
"""

from collections.abc import Sequence

from src.domain.entities.playlist import PlaylistEntry


def select_appendable_entries(
    current_entries: Sequence[PlaylistEntry],
    candidate_entries: Sequence[PlaylistEntry],
) -> list[PlaylistEntry]:
    """Return the candidate entries to append, deduped by canonical track id.

    An entry is appendable when it carries no canonical track id — unresolved
    positions (``track is None``) and id-less tracks never collide, so they are
    always kept — or its ``track.id`` is not already present in
    ``current_entries``. Source order is preserved.

    This is the workflow-append / overwrite-with-preservation dedup rule:
    re-appending a track already in the playlist is a no-op, while distinct
    unresolved source positions are retained so the playlist stays complete.
    (Manual add — ``AddPlaylistTracksUseCase`` — deliberately does NOT use this;
    it models repeated memberships as distinct rows.)
    """
    existing_track_ids = {
        entry.track.id
        for entry in current_entries
        if entry.track is not None and entry.track.id
    }
    return [
        entry
        for entry in candidate_entries
        if entry.track is None
        or not entry.track.id
        or entry.track.id not in existing_track_ids
    ]
