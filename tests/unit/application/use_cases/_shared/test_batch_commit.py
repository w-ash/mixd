"""Tests for the batch_commit helper.

Validates that commit_batch() dispatches to UoW implementations that support
BatchCommittable, and is a safe no-op for those that don't.
"""

from unittest.mock import AsyncMock

from src.application.use_cases._shared.batch_commit import commit_batch
from tests.fixtures.mocks import make_mock_uow


class TestCommitBatchHappyPath:
    """UoW implementations that support batch commits."""

    async def test_calls_commit_batch(self):
        """make_mock_uow() UoW -> commit_batch awaited once."""
        uow = make_mock_uow()

        await commit_batch(uow)

        uow.commit_batch.assert_awaited_once()


class TestCommitBatchNoOp:
    """UoW implementations without batch commit support."""

    async def test_noop_without_commit_batch(self):
        """UoW lacking commit_batch -> no error; not raising is the contract."""
        uow = AsyncMock(spec=[])

        await commit_batch(uow)
