"""Tests for the batch_commit helper.

Validates that commit_batch() dispatches to UoW implementations that support
BatchCommittable, and is a safe no-op for those that don't.
"""

from unittest.mock import AsyncMock

from src.application.use_cases._shared.batch_commit import (
    BatchCommittable,
    commit_batch,
)
from tests.fixtures.mocks import make_mock_uow


class TestCommitBatchHappyPath:
    """UoW implementations that support batch commits."""

    async def test_calls_commit_batch(self):
        """make_mock_uow() UoW -> commit_batch awaited once."""
        uow = make_mock_uow()

        await commit_batch(uow)

        uow.commit_batch.assert_awaited_once()

    async def test_mock_uow_satisfies_protocol(self):
        """make_mock_uow() should satisfy BatchCommittable at runtime."""
        uow = make_mock_uow()

        assert isinstance(uow, BatchCommittable)


class TestCommitBatchNoOp:
    """UoW implementations without batch commit support."""

    async def test_noop_without_commit_batch(self):
        """UoW lacking commit_batch -> no error."""
        uow = AsyncMock(spec=[])

        await commit_batch(uow)  # should not raise

    async def test_plain_mock_fails_protocol(self):
        """A bare AsyncMock(spec=[]) should NOT satisfy BatchCommittable."""
        uow = AsyncMock(spec=[])

        assert not isinstance(uow, BatchCommittable)
