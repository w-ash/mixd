"""Use case runner with session/UoW lifecycle management.

Provides a single entry point for executing use cases with proper database
session and unit-of-work wiring. Both CLI (via run_async) and FastAPI
(via Depends) should use this runner — zero business logic duplication.
"""

# pyright: reportExplicitAny=false
# Legitimate Any: use case results, OperationResult metadata, metric values

from collections.abc import Callable, Coroutine
from typing import Any

from src.domain.repositories import UnitOfWorkProtocol


async def execute_use_case[TResult](
    use_case_factory: Callable[[UnitOfWorkProtocol], Coroutine[Any, Any, TResult]],
) -> TResult:
    """Run a use case with proper session/UoW lifecycle.

    Creates a database session, wraps it in a UnitOfWork, passes it to the
    factory function, and returns the result. Session is committed on success,
    rolled back on failure.

    Args:
        use_case_factory: Async callable that receives a UoW and returns a result.
            Typically constructs and executes a use case.

    Returns:
        The result of the use case execution.

    Example::

        result = await execute_use_case(
            lambda uow: SyncLikesUseCase(uow).execute(command)
        )
    """
    from src.infrastructure.persistence.database.db_connection import get_session
    from src.infrastructure.persistence.repositories.factories import get_unit_of_work

    async with get_session() as session:
        uow = get_unit_of_work(session)
        return await use_case_factory(uow)
