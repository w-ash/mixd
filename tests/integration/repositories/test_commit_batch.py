"""Tests for DatabaseUnitOfWork.commit_batch() intermediate commit semantics.

Verifies that commit_batch() issues a real PostgreSQL COMMIT without marking
the UoW as fully committed, enabling incremental persistence in long-running
batch operations while preserving the __aexit__ auto-commit safety net.
"""

from uuid import uuid4

import pytest

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

    async def test_both_batches_persist(self, db_session):
        """Write A, commit_batch, write B, commit_batch — both survive."""
        uow = DatabaseUnitOfWork(db_session)

        async with uow:
            repo = uow.get_track_repository()

            saved_a = await repo.save_track(_make_test_track("A"))
            await uow.commit_batch()

            saved_b = await repo.save_track(_make_test_track("B"))
            await uow.commit_batch()

        # Both committed — re-read to prove durability
        uow2 = DatabaseUnitOfWork(db_session)
        async with uow2:
            repo2 = uow2.get_track_repository()
            found_a = await repo2.find_tracks_by_ids([saved_a.id])
            found_b = await repo2.find_tracks_by_ids([saved_b.id])
            assert saved_a.id in found_a
            assert saved_b.id in found_b

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

    async def test_committed_flag_stays_false(self, db_session):
        """commit_batch() must NOT set _committed, preserving auto-commit safety."""
        uow = DatabaseUnitOfWork(db_session)

        async with uow:
            repo = uow.get_track_repository()
            saved = await repo.save_track(_make_test_track("flag"))

            await uow.commit_batch()
            assert not uow._committed

    @pytest.mark.slow
    async def test_multiple_batches_with_final_commit(self, db_session):
        """Simulate a realistic import: N batch commits then a final commit()."""
        uow = DatabaseUnitOfWork(db_session)
        saved_ids = []

        async with uow:
            repo = uow.get_track_repository()

            for i in range(5):
                saved = await repo.save_track(_make_test_track(f"batch_{i}"))
                saved_ids.append(saved.id)
                await uow.commit_batch()

            await uow.commit()

        assert uow._committed

        uow2 = DatabaseUnitOfWork(db_session)
        async with uow2:
            repo2 = uow2.get_track_repository()
            found = await repo2.find_tracks_by_ids(saved_ids)
            assert len(found) == 5
