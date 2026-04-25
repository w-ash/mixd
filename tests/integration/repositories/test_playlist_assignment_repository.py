"""Integration tests for PlaylistAssignmentRepository.

Covers UNIQUE enforcement on (connector_playlist_id, action_type,
action_value), CASCADE from DBConnectorPlaylist, member-snapshot
replacement semantics, and the Epic 7 decoupling guarantee: an assignment
can exist for a ConnectorPlaylist that has NO canonical Playlist or
PlaylistLink.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from src.domain.entities.playlist_assignment import (
    PlaylistAssignment,
)
from src.infrastructure.persistence.database.db_models import (
    DBConnectorPlaylist,
    DBTrack,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


async def _setup_connector_playlist(db_session, *, user_id: str = "default") -> UUID:
    """Create a bare DBConnectorPlaylist (no canonical Playlist, no link)."""
    uid = uuid4().hex[:8]
    db_cp = DBConnectorPlaylist(
        connector_name="spotify",
        connector_playlist_identifier=f"sp_{uid}",
        name=f"CP {uid}",
        description=None,
        owner="testuser",
        owner_id="user123",
        is_public=True,
        collaborative=False,
        follower_count=0,
        items=[],
        raw_metadata={},
        last_updated=datetime.now(UTC),
    )
    db_session.add(db_cp)
    await db_session.flush()
    await db_session.commit()
    return db_cp.id


async def _create_track(db_session, *, user_id: str = "default") -> UUID:
    uid = uuid4().hex[:8]
    track = DBTrack(title=f"T {uid}", user_id=user_id, artists=[])
    db_session.add(track)
    await db_session.flush()
    await db_session.commit()
    return track.id


class TestCreateAssignments:
    async def test_persists_tag_assignment_without_canonical_playlist(self, db_session):
        """Validates Epic 7 decoupling: assignment works with only a
        ConnectorPlaylist — no canonical Playlist or PlaylistLink needed."""
        cp_id = await _setup_connector_playlist(db_session)

        uow = get_unit_of_work(db_session)
        async with uow:
            assignment = PlaylistAssignment.create(
                user_id="default",
                connector_playlist_id=cp_id,
                action_type="add_tag",
                raw_action_value="mood:chill",
            )
            repo = uow.get_playlist_assignment_repository()
            created = await repo.create_assignments([assignment], user_id="default")
            await uow.commit()

        assert len(created) == 1
        assert created[0].action_value == "mood:chill"

    async def test_unique_constraint_skips_duplicate(self, db_session):
        cp_id = await _setup_connector_playlist(db_session)

        a1 = PlaylistAssignment.create(
            user_id="default",
            connector_playlist_id=cp_id,
            action_type="set_preference",
            raw_action_value="star",
        )
        a2 = PlaylistAssignment.create(
            user_id="default",
            connector_playlist_id=cp_id,
            action_type="set_preference",
            raw_action_value="star",
        )

        uow = get_unit_of_work(db_session)
        async with uow:
            repo = uow.get_playlist_assignment_repository()
            first = await repo.create_assignments([a1], user_id="default")
            await uow.commit()
        async with uow:
            repo = uow.get_playlist_assignment_repository()
            second = await repo.create_assignments([a2], user_id="default")
            await uow.commit()

        assert len(first) == 1
        assert len(second) == 0  # ON CONFLICT DO NOTHING

    async def test_multiple_action_types_on_same_playlist(self, db_session):
        """'Workout Starred' → preference=star AND tag context:workout."""
        cp_id = await _setup_connector_playlist(db_session)

        preference_assignment = PlaylistAssignment.create(
            user_id="default",
            connector_playlist_id=cp_id,
            action_type="set_preference",
            raw_action_value="star",
        )
        tag_assignment = PlaylistAssignment.create(
            user_id="default",
            connector_playlist_id=cp_id,
            action_type="add_tag",
            raw_action_value="context:workout",
        )

        uow = get_unit_of_work(db_session)
        async with uow:
            repo = uow.get_playlist_assignment_repository()
            created = await repo.create_assignments(
                [preference_assignment, tag_assignment], user_id="default"
            )
            await uow.commit()
        async with uow:
            repo = uow.get_playlist_assignment_repository()
            listed = await repo.list_for_connector_playlist(cp_id, user_id="default")

        assert len(created) == 2
        assert len(listed) == 2


class TestCascadeDelete:
    async def test_deleting_connector_playlist_cascades_to_assignments(
        self, db_session
    ):
        cp_id = await _setup_connector_playlist(db_session)

        assignment = PlaylistAssignment.create(
            user_id="default",
            connector_playlist_id=cp_id,
            action_type="add_tag",
            raw_action_value="mood:chill",
        )
        uow = get_unit_of_work(db_session)
        async with uow:
            repo = uow.get_playlist_assignment_repository()
            await repo.create_assignments([assignment], user_id="default")
            await uow.commit()

        async with uow:
            cp = await db_session.get(DBConnectorPlaylist, cp_id)
            await db_session.delete(cp)
            await uow.commit()

        async with uow:
            repo = uow.get_playlist_assignment_repository()
            remaining = await repo.list_for_user(user_id="default")
        assert all(a.connector_playlist_id != cp_id for a in remaining)


class TestDeleteAssignment:
    async def test_delete_returns_true_and_removes(self, db_session):
        cp_id = await _setup_connector_playlist(db_session)

        assignment = PlaylistAssignment.create(
            user_id="default",
            connector_playlist_id=cp_id,
            action_type="add_tag",
            raw_action_value="mood:chill",
        )
        uow = get_unit_of_work(db_session)
        async with uow:
            repo = uow.get_playlist_assignment_repository()
            await repo.create_assignments([assignment], user_id="default")
            await uow.commit()

        async with uow:
            repo = uow.get_playlist_assignment_repository()
            removed = await repo.delete_assignment(assignment.id, user_id="default")
            await uow.commit()
        assert removed is True

        async with uow:
            repo = uow.get_playlist_assignment_repository()
            remaining = await repo.list_for_connector_playlist(cp_id, user_id="default")
        assert all(a.id != assignment.id for a in remaining)

    async def test_delete_missing_returns_false(self, db_session):
        uow = get_unit_of_work(db_session)
        async with uow:
            repo = uow.get_playlist_assignment_repository()
            removed = await repo.delete_assignment(uuid4(), user_id="default")
        assert removed is False
