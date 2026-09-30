"""Tests for writes across Unit of Work boundaries.

Every repository a UoW hands out runs on the UoW's session, so one
``rollback()`` discards the writes of all of them together; a later UoW
updates what an earlier one committed.
"""

from datetime import UTC, datetime
from uuid import uuid4

from attrs import evolve
import pytest

from src.domain.entities.playlist import Playlist, PlaylistEntry
from src.domain.exceptions import NotFoundError
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import TEST_USER_ID, make_track


class TestBulkUoWPatterns:
    """Cross-repository transaction boundaries within one Unit of Work."""

    async def test_rollback_discards_writes_from_every_repository_in_the_uow(
        self, db_session
    ):
        """Tracks (track repo) and the playlist that uses them (playlist repo)
        are written in one UoW; a rollback removes both, because both repos
        share the UoW's session and transaction.
        """
        uow = get_unit_of_work(db_session)

        async with uow:
            track_repo = uow.get_track_repository()
            playlist_repo = uow.get_playlist_repository()

            tracks_to_save = [
                make_track(
                    title=f"TEST_UoW_Track_{i}_{uuid4()}",
                    artist=f"TEST_UoW_Artist_{i}_{uuid4()}",
                    connector_track_identifiers={
                        "spotify": f"spotify_id_{i}_{uuid4()}"
                    },
                )
                for i in range(3)
            ]

            saved_tracks = []
            for track in tracks_to_save:
                saved = await track_repo.save_track(track)
                saved_tracks.append(saved)
            track_ids = [t.id for t in saved_tracks]
            for track in saved_tracks:
                # The DB pseudo-connector only: a canonical's connector ids
                # come from its mappings, which save_track does not write.
                assert set(track.connector_track_identifiers) == {"db"}

            playlist = Playlist(
                name=f"TEST_UoW_Playlist_{uuid4()}",
                entries=[
                    PlaylistEntry(track=track, added_at=datetime.now(UTC))
                    for track in saved_tracks
                ],
                user_id=TEST_USER_ID,
            )
            saved_playlist = await playlist_repo.save_playlist(playlist)
            assert len(saved_playlist.entries) == 3
            for entry in saved_playlist.entries:
                assert entry.track.id in track_ids
            await uow.rollback()

        async with uow:
            track_repo = uow.get_track_repository()
            playlist_repo = uow.get_playlist_repository()
            for track_id in track_ids:
                with pytest.raises(NotFoundError, match="not found"):
                    await track_repo.get_by_id(track_id)
            with pytest.raises(NotFoundError, match="not found"):
                await playlist_repo.get_by_id(saved_playlist.id)

    async def test_update_of_a_committed_track_rewrites_it_in_place(self, db_session):
        """A later UoW updates a committed track's own row, beside a new insert.

        The update rides the optimistic-locking arm (``evolve`` keeps the
        version); a version-0 row re-naming a claimed spotify id would be
        refused, not upserted.
        """
        uow = get_unit_of_work(db_session)

        async with uow:
            track_repo = uow.get_track_repository()
            initial_track = make_track(
                title=f"TEST_Initial_{uuid4()}",
                artist=f"TEST_Artist_{uuid4()}",
                connector_track_identifiers={"spotify": f"spotify_{uuid4()}"},
            )
            saved_initial = await track_repo.save_track(initial_track)
            await uow.commit()

        new_title = f"TEST_Updated_{uuid4()}"
        async with uow:
            track_repo = uow.get_track_repository()
            saved_updated = await track_repo.save_track(
                evolve(saved_initial, title=new_title)
            )
            saved_new = await track_repo.save_track(
                make_track(
                    title=f"TEST_New_{uuid4()}",
                    artist=f"TEST_Artist_{uuid4()}",
                    connector_track_identifiers={},
                )
            )
            await uow.commit()

        async with uow:
            reloaded = await uow.get_track_repository().get_by_id(saved_initial.id)

        assert saved_updated.id == saved_initial.id
        assert saved_updated.title == new_title
        assert reloaded.title == new_title
        assert saved_new.id != saved_initial.id
