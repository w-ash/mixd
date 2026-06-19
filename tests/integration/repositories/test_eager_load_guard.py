"""The ``lazy="raise_on_sql"`` guard fires on an accidental lazy load.

Proves the v0.8.6 eager-load hardening catches a forgotten ``selectinload`` at
the failing query (fail-loud) rather than degrading to a silent ``[]``/``None``.
Without the guard these reads would emit hidden lazy SQL or, in async, surface a
confusing ``MissingGreenlet`` far from the cause.
"""

from datetime import UTC, datetime
from uuid import uuid7

import pytest
from sqlalchemy import select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.workflow import (
    Workflow,
    WorkflowDef,
    WorkflowRun,
    WorkflowRunNode,
    WorkflowTaskDef,
)
from src.infrastructure.persistence.database.db_models import (
    DBPlaylist,
    DBPlaylistTrack,
    DBTrack,
    DBWorkflowRun,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from src.infrastructure.persistence.repositories.workflow.core import WorkflowRepository
from src.infrastructure.persistence.repositories.workflow.runs import (
    WorkflowRunRepository,
)
from tests.fixtures import make_track


class TestRaiseOnSqlGuardFires:
    async def test_forgotten_eager_load_on_track_relationship_raises(self, db_session):
        """``DBTrack.tags`` is guarded and not eager-loaded by default — a raw
        re-query that forgets to load it must raise, not silently return ``[]``.
        """
        uow = get_unit_of_work(db_session)
        async with uow:
            repo = uow.get_track_repository()
            saved = await repo.save_track(
                make_track(
                    title=f"TEST_guard_{uuid7()}",
                    artist="A",
                    connector_track_identifiers={},
                )
            )
            await uow.commit()

        db_session.expire_all()
        db = (
            await db_session.execute(select(DBTrack).where(DBTrack.id == saved.id))
        ).scalar_one()
        with pytest.raises(InvalidRequestError):
            _ = db.tags

    async def test_forgotten_eager_load_on_workflow_run_nodes_raises(self, db_session):
        """``DBWorkflowRun.nodes`` is the one relationship read by direct
        attribute access (run_mapper). Production paths eager-load it; a query
        that forgets must fail loud rather than lazy-load.
        """
        wf = await WorkflowRepository(db_session).save_workflow(
            Workflow(
                user_id="default",
                definition=WorkflowDef(
                    id="t",
                    name="T",
                    tasks=[WorkflowTaskDef(id="s1", type="source.liked_tracks")],
                ),
            )
        )
        run = await WorkflowRunRepository(db_session).create_run(
            WorkflowRun(
                workflow_id=wf.id,
                status="pending",
                definition_snapshot=WorkflowDef(id="t", name="T"),
                nodes=[
                    WorkflowRunNode(
                        node_id="s1",
                        node_type="source.liked_tracks",
                        execution_order=1,
                    )
                ],
            )
        )

        db_session.expire_all()
        db = (
            await db_session.execute(
                select(DBWorkflowRun).where(DBWorkflowRun.id == run.id)
            )
        ).scalar_one()
        with pytest.raises(InvalidRequestError):
            _ = db.nodes


async def _make_playlist_with_track(session: AsyncSession) -> tuple:
    """Persist a playlist with one track row; return (playlist_id, track_id)."""
    now = datetime.now(UTC)
    playlist = DBPlaylist(
        name="Guard PL", track_count=1, created_at=now, updated_at=now
    )
    track = DBTrack(
        title="Guard Track", artists={"names": ["A"]}, created_at=now, updated_at=now
    )
    session.add_all([playlist, track])
    await session.flush()
    session.add(
        DBPlaylistTrack(
            playlist_id=playlist.id,
            track_id=track.id,
            sort_key="a00000000",
            created_at=now,
            updated_at=now,
        )
    )
    await session.flush()
    return playlist.id, track.id


class TestGuardDoesNotBreakProductionPaths:
    """The guard must fire on *forgotten* loads without breaking real cascades/loads.

    These complement the fire tests above: ``raise_on_sql`` only earns its keep if
    the legitimate delete-cascade and eager-load paths still work under it. A
    relationship that gained ``raise_on_sql`` but forgot ``passive_deletes`` would
    raise here on delete; a mapper that dropped a ``selectinload`` would return an
    empty collection here instead of raising (the silent failure the epic targets).
    """

    async def test_delete_cascade_succeeds_under_guard(self, db_session: AsyncSession):
        """Deleting a playlist removes its (guarded, delete-orphan) track rows.

        ``DBPlaylist.tracks`` is ``cascade="all, delete-orphan"`` +
        ``lazy="raise_on_sql"`` + ``passive_deletes=True`` — the trio that only
        works if the DB FK carries ``ON DELETE CASCADE``. The delete must neither
        raise (no lazy load of children) nor orphan the child rows.
        """
        playlist_id, _ = await _make_playlist_with_track(db_session)

        playlist_repo = get_unit_of_work(db_session).get_playlist_repository()
        deleted = await playlist_repo.delete_playlist(playlist_id, user_id="default")
        assert deleted is True

        db_session.expire_all()
        remaining = (
            (
                await db_session.execute(
                    select(DBPlaylistTrack).where(
                        DBPlaylistTrack.playlist_id == playlist_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert remaining == []

    async def test_eager_load_path_returns_nonempty_collection(
        self, db_session: AsyncSession
    ):
        """The production loader returns a populated collection, not a silent ``[]``.

        Because mapper reads go through zero-IO ``loaded_*`` helpers, a dropped
        ``selectinload`` would degrade to ``[]`` rather than raise — so a positive
        assertion is the only thing that catches it.
        """
        playlist_id, _ = await _make_playlist_with_track(db_session)

        db_session.expire_all()
        playlist_repo = get_unit_of_work(db_session).get_playlist_repository()
        playlist = await playlist_repo.get_playlist_by_id(
            playlist_id, user_id="default"
        )

        assert len(playlist.tracks) == 1
