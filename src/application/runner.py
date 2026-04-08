"""Use case runner with session/UoW lifecycle management.

Provides a single entry point for executing use cases with proper database
session and unit-of-work wiring. Both CLI (via run_async) and FastAPI
(via Depends) should use this runner — zero business logic duplication.
"""

# Legitimate Any: use case results, OperationResult metadata, metric values

from collections.abc import Callable, Coroutine
from typing import Any

from src.domain.repositories import UnitOfWorkProtocol


async def execute_use_case[TResult](
    use_case_factory: Callable[[UnitOfWorkProtocol], Coroutine[Any, Any, TResult]],
    user_id: str | None = None,
) -> TResult:
    """Run a use case with proper session/UoW lifecycle.

    Creates a database session, wraps it in a UnitOfWork, passes it to the
    factory function, and returns the result. Session is committed on success,
    rolled back on failure.

    When ``user_id`` is provided (API path), sets the ContextVar that feeds
    the ``after_begin`` event handler — this calls ``SET LOCAL app.user_id``
    on the PostgreSQL transaction for RLS enforcement.  When ``None`` (CLI
    path), the ContextVar stays at its default (``"default"``).

    Args:
        use_case_factory: Async callable that receives a UoW and returns a result.
            Typically constructs and executes a use case.
        user_id: Neon Auth ``sub`` claim from the route handler, or ``None``
            for CLI callers.

    Returns:
        The result of the use case execution.

    Example::

        result = await execute_use_case(
            lambda uow: SyncLikesUseCase(uow).execute(command),
            user_id=user_id,
        )
    """
    from src.infrastructure.persistence.database.db_connection import get_session
    from src.infrastructure.persistence.database.user_context import user_context
    from src.infrastructure.persistence.repositories.factories import get_unit_of_work

    async with get_session() as session:
        uow = get_unit_of_work(session)
        if user_id is not None:
            with user_context(user_id):
                return await use_case_factory(uow)
        return await use_case_factory(uow)
