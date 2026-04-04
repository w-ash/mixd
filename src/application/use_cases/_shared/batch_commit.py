"""Helper for incremental batch commits in long-running import operations.

Provides a type-safe way to call commit_batch() on UoW implementations that
support it, without requiring the domain UnitOfWorkProtocol to know about
batch semantics.
"""

from typing import Protocol, runtime_checkable

from src.domain.repositories import UnitOfWorkProtocol


@runtime_checkable
class BatchCommittable(Protocol):
    """UoW that supports intermediate batch commits."""

    async def commit_batch(self) -> None: ...


async def commit_batch(uow: UnitOfWorkProtocol) -> None:
    """Commit the current batch if the UoW supports it.

    Calls ``uow.commit_batch()`` on implementations that provide incremental
    commit support (e.g., ``DatabaseUnitOfWork``). No-op for implementations
    that don't (plain test doubles without the method wired).
    """
    if isinstance(uow, BatchCommittable):
        await uow.commit_batch()
