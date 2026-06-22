"""Integration tests: unresolved playlist entries persist and round-trip.

Proves the persistence half of "an imported playlist is always complete": a
source position whose connector track has no canonical match is stored as a
first-class row (track_id NULL + unresolved_metadata) rather than silently
dropped, keeps its slot and order alongside resolved entries, and survives a
re-save with a stable membership id.
"""

from datetime import UTC, datetime

import attrs
from sqlalchemy import select

from src.domain.entities.playlist import (
    ConnectorTrackRef,
    Playlist,
    PlaylistEntry,
)
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBPlaylistTrack,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import make_track


def _mixed_playlist() -> Playlist:
    """Resolved A · unresolved (local file) · resolved B — order matters."""
    return Playlist(
        name="Imported With Holes",
        user_id="default",
        entries=[
            PlaylistEntry(track=make_track(title="Resolved A")),
            PlaylistEntry(
                track=None,
                connector_track_ref=ConnectorTrackRef(
                    connector_name="spotify",
                    connector_track_identifier="local:ghost",
                    title="Ghost Local File",
                    artists=("Some Artist",),
                ),
            ),
            PlaylistEntry(track=make_track(title="Resolved B")),
        ],
    )


class TestUnresolvedRoundTrip:
    async def test_unresolved_entry_persists_and_round_trips(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_playlist_repository()

        saved = await repo.save_playlist(_mixed_playlist())
        loaded = await repo.get_playlist_by_id(saved.id, user_id="default")

        # Complete: all three positions survive, in order.
        assert len(loaded.entries) == 3
        assert loaded.track_count == 3
        assert loaded.unresolved_count == 1
        assert loaded.entries[0].is_resolved
        assert not loaded.entries[1].is_resolved
        assert loaded.entries[2].is_resolved

        # The unresolved position keeps its display snapshot for the UI.
        ref = loaded.entries[1].connector_track_ref
        assert ref is not None
        assert ref.connector_track_identifier == "local:ghost"
        assert loaded.entries[1].display_title == "Ghost Local File"

        # Resolved-only views exclude the hole.
        assert len(loaded.tracks) == 2

    async def test_unresolved_without_connector_row_has_null_fk(self, db_session):
        """A local/unavailable track with no connector_tracks row still persists."""
        uow = get_unit_of_work(db_session)
        repo = uow.get_playlist_repository()

        saved = await repo.save_playlist(_mixed_playlist())

        stmt = select(DBPlaylistTrack).where(
            DBPlaylistTrack.playlist_id == saved.id,
            DBPlaylistTrack.track_id.is_(None),
        )
        unresolved_rows = list((await db_session.scalars(stmt)).all())
        assert len(unresolved_rows) == 1
        # Best-effort FK is NULL (no connector_tracks row), but the row exists.
        assert unresolved_rows[0].connector_track_id is None
        assert unresolved_rows[0].unresolved_metadata is not None

    async def test_unresolved_links_fk_when_connector_track_exists(self, db_session):
        """When the connector track IS in the DB, the re-resolution FK is populated."""
        now = datetime.now(UTC)
        ct = DBConnectorTrack(
            connector_name="spotify",
            connector_track_identifier="local:ghost",
            title="Ghost Local File",
            artists={"names": ["Some Artist"]},
            raw_metadata={},
            last_updated=now,
            created_at=now,
            updated_at=now,
        )
        db_session.add(ct)
        await db_session.flush()

        uow = get_unit_of_work(db_session)
        repo = uow.get_playlist_repository()
        saved = await repo.save_playlist(_mixed_playlist())

        stmt = select(DBPlaylistTrack).where(
            DBPlaylistTrack.playlist_id == saved.id,
            DBPlaylistTrack.track_id.is_(None),
        )
        row = (await db_session.scalars(stmt)).one()
        assert row.connector_track_id == ct.id

    async def test_resave_preserves_unresolved_membership_id(self, db_session):
        """Re-saving the loaded playlist keeps the unresolved row's id + added_at."""
        uow = get_unit_of_work(db_session)
        repo = uow.get_playlist_repository()

        saved = await repo.save_playlist(_mixed_playlist())

        stmt = select(DBPlaylistTrack).where(
            DBPlaylistTrack.playlist_id == saved.id,
            DBPlaylistTrack.track_id.is_(None),
        )
        original_row = (await db_session.scalars(stmt)).one()
        original_id = original_row.id

        # Reload (entries now carry their DB membership ids) and re-save unchanged.
        loaded = await repo.get_playlist_by_id(saved.id, user_id="default")
        await repo.update_playlist(saved.id, loaded, user_id="default")

        after = (await db_session.scalars(stmt)).one()
        assert after.id == original_id  # same membership record reused, not replaced

    async def test_unresolved_entry_hydrates_on_resave(self, db_session):
        """Repair's persistence path: re-saving the loaded playlist with the
        middle (unresolved) entry now carrying a track resolves that position —
        track_id set, no unresolved rows left, order + count preserved.

        This is the transition RepairUnresolvedEntriesUseCase relies on (it reuses
        ``update_playlist`` rather than a bespoke hydrate query). The membership
        row is reissued (the resolved/unresolved membership key differs), but the
        position and the entry's ``added_at`` ride on the entry, so the user-facing
        result is correct.
        """
        uow = get_unit_of_work(db_session)
        repo = uow.get_playlist_repository()

        saved = await repo.save_playlist(_mixed_playlist())

        # Reload, then hydrate the middle (unresolved) entry to a real track.
        loaded = await repo.get_playlist_by_id(saved.id, user_id="default")
        entries = list(loaded.entries)
        assert not entries[1].is_resolved
        entries[1] = attrs.evolve(
            entries[1], track=make_track(title="Now Matched"), connector_track_ref=None
        )
        await repo.update_playlist(
            saved.id, attrs.evolve(loaded, entries=entries), user_id="default"
        )

        reloaded = await repo.get_playlist_by_id(saved.id, user_id="default")
        assert reloaded.unresolved_count == 0
        assert len(reloaded.entries) == 3
        assert [e.is_resolved for e in reloaded.entries] == [True, True, True]
        assert reloaded.entries[1].track is not None
        assert reloaded.entries[1].track.title == "Now Matched"

        # No unresolved rows remain in the DB for this playlist.
        leftover = list(
            await db_session.scalars(
                select(DBPlaylistTrack).where(
                    DBPlaylistTrack.playlist_id == saved.id,
                    DBPlaylistTrack.track_id.is_(None),
                )
            )
        )
        assert leftover == []
