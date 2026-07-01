"""Integration tests for manual playlist track editing (v0.8.11).

Exercises the three editing use cases against the real repository so the
identity-preserving consumption matcher is actually run. We assert via the
loaded ``PlaylistEntry.id`` values — these mirror ``DBPlaylistTrack.id``, so
entry-id stability across an edit *is* DB record-id stability.

Companion to ``test_playlist_update_preservation_bugs_v2.py`` (which pins the
replace path); do not break that one.
"""

from uuid import uuid7

import pytest

from src.application.use_cases.add_playlist_tracks import (
    AddPlaylistTracksCommand,
    AddPlaylistTracksUseCase,
)
from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistCommand,
    CreateCanonicalPlaylistUseCase,
)
from src.application.use_cases.remove_playlist_entries import (
    RemovePlaylistEntriesCommand,
    RemovePlaylistEntriesUseCase,
)
from src.application.use_cases.reorder_playlist_entries import (
    ReorderPlaylistEntriesCommand,
    ReorderPlaylistEntriesUseCase,
)
from src.domain.entities.playlist import Playlist
from src.domain.entities.track import Track, TrackList
from src.domain.exceptions import NotFoundError
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import make_mock_metric_config, make_track

_MOCK_METRIC_CONFIG = make_mock_metric_config()


async def _persist_track(uow, title: str, *, user_id: str = "default") -> Track:
    """Insert a canonical track and return it with its DB id."""
    async with uow:
        track = await uow.get_track_repository().save_track(
            make_track(title=title, artist=f"{title} Artist", user_id=user_id)
        )
        await uow.commit()
    return track


async def _make_playlist(
    uow, tracks: list[Track], *, user_id: str = "default", name: str = "P"
) -> Playlist:
    async with uow:
        result = await CreateCanonicalPlaylistUseCase(
            metric_config=_MOCK_METRIC_CONFIG
        ).execute(
            CreateCanonicalPlaylistCommand(
                user_id=user_id, name=name, tracklist=TrackList(tracks=tracks)
            ),
            uow,
        )
        await uow.commit()
    return result.playlist


async def _load(uow, playlist_id, *, user_id: str = "default") -> Playlist:
    async with uow:
        return await uow.get_playlist_repository().get_playlist_by_id(
            playlist_id, user_id=user_id
        )


class TestAddTracks:
    async def test_add_appends_and_allows_duplicates(self, db_session):
        """Manual add appends in order and a repeated track becomes a 2nd entry."""
        uow = get_unit_of_work(db_session)
        a = await _persist_track(uow, "A")
        b = await _persist_track(uow, "B")
        playlist = await _make_playlist(uow, [a, b])

        # Add A again — duplicates are intentional for manual curation.
        async with uow:
            await AddPlaylistTracksUseCase().execute(
                AddPlaylistTracksCommand(
                    user_id="default", playlist_id=playlist.id, track_ids=[a.id]
                ),
                uow,
            )
            await uow.commit()

        loaded = await _load(uow, playlist.id)
        track_ids = [e.track.id for e in loaded.entries if e.track is not None]
        entry_ids = [e.id for e in loaded.entries]
        assert track_ids == [a.id, b.id, a.id]  # appended to the end
        assert len(set(entry_ids)) == 3  # each membership has a distinct id

    async def test_add_unknown_track_raises_not_found(self, db_session):
        uow = get_unit_of_work(db_session)
        playlist = await _make_playlist(uow, [await _persist_track(uow, "A")])
        with pytest.raises(NotFoundError):
            async with uow:
                await AddPlaylistTracksUseCase().execute(
                    AddPlaylistTracksCommand(
                        user_id="default", playlist_id=playlist.id, track_ids=[uuid7()]
                    ),
                    uow,
                )

    async def test_add_other_users_track_raises_not_found(self, db_session):
        """find_tracks_by_ids is unscoped, so the use case must gate on ownership."""
        uow = get_unit_of_work(db_session)
        playlist = await _make_playlist(uow, [await _persist_track(uow, "Mine")])
        foreign = await _persist_track(uow, "Theirs", user_id="other")
        with pytest.raises(NotFoundError):
            async with uow:
                await AddPlaylistTracksUseCase().execute(
                    AddPlaylistTracksCommand(
                        user_id="default",
                        playlist_id=playlist.id,
                        track_ids=[foreign.id],
                    ),
                    uow,
                )


class TestRemoveEntries:
    async def test_remove_one_of_two_identical_leaves_the_other(self, db_session):
        """Removing one duplicate entry by id leaves its twin (and its identity)."""
        uow = get_unit_of_work(db_session)
        a = await _persist_track(uow, "A")
        b = await _persist_track(uow, "B")
        playlist = await _make_playlist(uow, [a, b, a])  # A appears twice

        loaded = await _load(uow, playlist.id)
        first_a = loaded.entries[0]  # the duplicate we'll remove
        survivor_a = loaded.entries[2]  # the twin that must remain
        assert first_a.track is not None
        assert first_a.track.id == a.id

        async with uow:
            await RemovePlaylistEntriesUseCase().execute(
                RemovePlaylistEntriesCommand(
                    user_id="default",
                    playlist_id=playlist.id,
                    entry_ids=[first_a.id],
                ),
                uow,
            )
            await uow.commit()

        after = await _load(uow, playlist.id)
        ids = [e.id for e in after.entries]
        track_ids = [e.track.id for e in after.entries if e.track is not None]
        assert first_a.id not in ids
        assert survivor_a.id in ids  # the twin kept its record identity
        assert track_ids == [b.id, a.id]

    async def test_batch_remove_removes_only_named_entries(self, db_session):
        uow = get_unit_of_work(db_session)
        a = await _persist_track(uow, "A")
        b = await _persist_track(uow, "B")
        c = await _persist_track(uow, "C")
        playlist = await _make_playlist(uow, [a, b, c])

        loaded = await _load(uow, playlist.id)
        by_track = {e.track.id: e.id for e in loaded.entries if e.track is not None}

        async with uow:
            await RemovePlaylistEntriesUseCase().execute(
                RemovePlaylistEntriesCommand(
                    user_id="default",
                    playlist_id=playlist.id,
                    entry_ids=[by_track[a.id], by_track[c.id]],
                ),
                uow,
            )
            await uow.commit()

        after = await _load(uow, playlist.id)
        assert [e.track.id for e in after.entries if e.track is not None] == [b.id]

    async def test_remove_stale_entry_id_raises_not_found(self, db_session):
        uow = get_unit_of_work(db_session)
        playlist = await _make_playlist(uow, [await _persist_track(uow, "A")])
        with pytest.raises(NotFoundError):
            async with uow:
                await RemovePlaylistEntriesUseCase().execute(
                    RemovePlaylistEntriesCommand(
                        user_id="default",
                        playlist_id=playlist.id,
                        entry_ids=[uuid7()],
                    ),
                    uow,
                )


class TestReorderEntries:
    async def test_reorder_preserves_identity_and_added_at(self, db_session):
        """Full-list reorder renumbers order while keeping each entry's id + added_at."""
        uow = get_unit_of_work(db_session)
        a = await _persist_track(uow, "A")
        b = await _persist_track(uow, "B")
        c = await _persist_track(uow, "C")
        playlist = await _make_playlist(uow, [a, b, c])

        loaded = await _load(uow, playlist.id)
        before = {
            e.id: (e.track.id if e.track else None, e.added_at) for e in loaded.entries
        }
        # New order: C, A, B
        new_order = [loaded.entries[2].id, loaded.entries[0].id, loaded.entries[1].id]

        async with uow:
            await ReorderPlaylistEntriesUseCase().execute(
                ReorderPlaylistEntriesCommand(
                    user_id="default", playlist_id=playlist.id, entry_ids=new_order
                ),
                uow,
            )
            await uow.commit()

        after = await _load(uow, playlist.id)
        assert [e.id for e in after.entries] == new_order  # order applied
        for entry in after.entries:
            # Same entry id ⇒ same DB record; track + added_at unchanged.
            assert before[entry.id] == (
                entry.track.id if entry.track else None,
                entry.added_at,
            )

    async def test_reorder_mismatched_set_raises_not_found(self, db_session):
        """A list that isn't an exact permutation of current entries is a stale view."""
        uow = get_unit_of_work(db_session)
        a = await _persist_track(uow, "A")
        b = await _persist_track(uow, "B")
        playlist = await _make_playlist(uow, [a, b])
        loaded = await _load(uow, playlist.id)

        with pytest.raises(NotFoundError):
            async with uow:
                await ReorderPlaylistEntriesUseCase().execute(
                    ReorderPlaylistEntriesCommand(
                        user_id="default",
                        playlist_id=playlist.id,
                        # Drops one id and injects a stranger → not a permutation.
                        entry_ids=[loaded.entries[0].id, uuid7()],
                    ),
                    uow,
                )
