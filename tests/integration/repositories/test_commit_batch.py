"""Tests for DatabaseUnitOfWork.commit_batch() intermediate commit semantics.

Verifies that commit_batch() issues a real PostgreSQL COMMIT without marking
the UoW as fully committed, enabling incremental persistence in long-running
batch operations while preserving the __aexit__ auto-commit safety net.

The ``db_session`` fixture runs the session inside a savepoint, so a session
commit releases that savepoint and a rollback returns to the last release —
the same boundary a real COMMIT draws.
"""

from uuid import uuid4

from src.domain.entities import Track
from src.infrastructure.persistence.unit_of_work import DatabaseUnitOfWork
from tests.fixtures import make_track


def _make_test_track(suffix: str) -> Track:
    uid = uuid4()
    return make_track(
        title=f"TEST_commit_batch_{suffix}_{uid}",
        artist=f"TEST_Artist_{suffix}_{uid}",
    )


class TestCommitBatch:
    """Test commit_batch() intermediate commit behaviour."""

    async def test_rollback_does_not_undo_committed_batch(self, db_session):
        """Write A, commit_batch, write B, rollback — A persists, B does not."""
        uow = DatabaseUnitOfWork(db_session)

        async with uow:
            repo = uow.get_track_repository()

            saved_a = await repo.save_track(_make_test_track("A"))
            await uow.commit_batch()

            saved_b = await repo.save_track(_make_test_track("B"))
            await uow.rollback()
            await uow.commit()  # prevent __aexit__ auto-commit of rolled-back state

        uow2 = DatabaseUnitOfWork(db_session)
        async with uow2:
            repo2 = uow2.get_track_repository()
            found_a = await repo2.find_tracks_by_ids([saved_a.id])
            found_b = await repo2.find_tracks_by_ids([saved_b.id])
            assert saved_a.id in found_a, "Committed batch A should survive rollback"
            assert saved_b.id not in found_b, (
                "Uncommitted batch B should be rolled back"
            )

    async def test_exit_still_commits_writes_made_after_a_batch_commit(
        self, db_session
    ):
        """commit_batch() must leave the __aexit__ auto-commit armed.

        A write after the last batch commit, with no explicit commit(), must
        still be committed when the context exits cleanly — so a later rollback
        of the session cannot remove it.
        """
        uow = DatabaseUnitOfWork(db_session)

        async with uow:
            repo = uow.get_track_repository()
            _ = await repo.save_track(_make_test_track("batched"))
            await uow.commit_batch()
            trailing = await repo.save_track(_make_test_track("trailing"))

        await db_session.rollback()

        uow2 = DatabaseUnitOfWork(db_session)
        async with uow2:
            found = await uow2.get_track_repository().find_tracks_by_ids([trailing.id])
            assert trailing.id in found, "exit must auto-commit the trailing write"
