"""Repository decorator for standardizing DB operations.

This module provides decorators for repository methods that handle common
database operations including:
- Structured logging with context and timing information
- Comprehensive error handling with appropriate error classification
- Consistent performance monitoring and debugging support

The decorators help enforce a consistent pattern for all database operations
while reducing repetitive error-handling code.
"""

import asyncio
from collections.abc import Callable, Coroutine
import functools
import inspect
import time
from typing import Any, TypeGuard

from sqlalchemy.exc import (
    DatabaseError,
    IntegrityError,
    MultipleResultsFound,
    NoResultFound,
    OperationalError,
    SQLAlchemyError,
    TimeoutError,
)

from src.config import get_logger

# Initialize logger
logger = get_logger(__name__)


def is_awaitable(value: Any) -> TypeGuard[Coroutine[Any, Any, Any]]:
    """Type guard for better asyncio.iscoroutine handling."""
    return asyncio.iscoroutine(value)


def db_operation(operation_name: str | None = None):
    """Decorate repository methods with consistent logging and error handling.

    Args:
        operation_name: Optional name for the operation (defaults to function name)

    Returns:
        A decorator function that wraps async repository methods

    Example:
        @db_operation("get_user")
        async def get_user_by_id(self, user_id: int) -> User:
            ...
    """

    def decorator[**P, T](
        func: Callable[P, Coroutine[Any, Any, T]],
    ) -> Callable[P, Coroutine[Any, Any, T]]:
        """Wrap an async repository method with logging, timing and error handling."""
        func_name = operation_name or func.__name__

        if not inspect.iscoroutinefunction(func):
            raise TypeError(
                f"db_operation can only be used with async functions, but {func_name} is not async",
            )

        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            """Execute the repository method with logging and error handling."""
            start_time = time.perf_counter()

            # Extract repository name from first argument (self)
            repo_name = args[0].__class__.__name__ if args else "Repository"

            # Build context for logging
            context = _build_log_context(kwargs)

            try:
                # Start timing
                logger.trace(
                    f"DB operation starting: {repo_name}.{func_name}",
                    operation=func_name,
                    **context,
                )

                # Call the original function
                result = await func(*args, **kwargs)

                # Log success with timing
                exec_time = (time.perf_counter() - start_time) * 1000
                logger.trace(
                    f"DB operation completed: {repo_name}.{func_name}",
                    operation=func_name,
                    exec_time_ms=exec_time,
                    **context,
                )

            except NoResultFound as e:
                # Handle "not found" as expected case with debug logging
                exec_time = (time.perf_counter() - start_time) * 1000
                logger.debug(
                    f"DB record not found: {repo_name}.{func_name}",
                    operation=func_name,
                    error=str(e),
                    exec_time_ms=exec_time,
                    **context,
                )
                raise

            except MultipleResultsFound as e:
                # Handle multiple results found with warning
                exec_time = (time.perf_counter() - start_time) * 1000
                logger.warning(
                    f"Multiple results found: {repo_name}.{func_name}",
                    operation=func_name,
                    error=str(e),
                    exec_time_ms=exec_time,
                    **context,
                )
                raise

            except IntegrityError as e:
                # Handle constraint violations and other integrity errors
                exec_time = (time.perf_counter() - start_time) * 1000
                logger.warning(
                    f"DB integrity error: {repo_name}.{func_name}",
                    operation=func_name,
                    error=str(e),
                    exec_time_ms=exec_time,
                    **context,
                )
                raise

            except TimeoutError as e:
                # Handle query/connection timeouts
                exec_time = (time.perf_counter() - start_time) * 1000
                logger.error(
                    f"DB timeout error: {repo_name}.{func_name}",
                    operation=func_name,
                    error=str(e),
                    exec_time_ms=exec_time,
                    **context,
                )
                raise

            except OperationalError as e:
                # Handle connection/operational errors
                exec_time = (time.perf_counter() - start_time) * 1000
                logger.error(
                    f"DB operational error: {repo_name}.{func_name}",
                    operation=func_name,
                    error=str(e),
                    exec_time_ms=exec_time,
                    **context,
                )
                raise

            except DatabaseError as e:
                # Handle other database errors
                exec_time = (time.perf_counter() - start_time) * 1000
                logger.error(
                    f"DB error: {repo_name}.{func_name}",
                    operation=func_name,
                    error=str(e),
                    exec_time_ms=exec_time,
                    **context,
                )
                raise

            except SQLAlchemyError as e:
                # Handle any other SQLAlchemy-specific errors
                exec_time = (time.perf_counter() - start_time) * 1000
                logger.error(
                    f"SQLAlchemy error: {repo_name}.{func_name}",
                    operation=func_name,
                    error=str(e),
                    exec_time_ms=exec_time,
                    **context,
                )
                raise

            except ValueError as e:
                # Handle domain value errors (e.g. "not found" from get_by_id)
                exec_time = (time.perf_counter() - start_time) * 1000
                logger.debug(
                    f"Domain value error: {repo_name}.{func_name}",
                    operation=func_name,
                    error=str(e),
                    exec_time_ms=exec_time,
                    **context,
                )
                raise

            except Exception as e:
                # Handle unexpected errors
                exec_time = (time.perf_counter() - start_time) * 1000
                logger.exception(
                    f"Unhandled exception in {repo_name}.{func_name}",
                    operation=func_name,
                    error=str(e),
                    exec_time_ms=exec_time,
                    **context,
                )
                raise
            else:
                return result

        return wrapper

    return decorator


def _build_log_context(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Build a context dictionary for logging from function kwargs.

    Args:
        kwargs: Function keyword arguments

    Returns:
        A dictionary with loggable values extracted from kwargs
    """
    # Extract ID parameters specifically
    id_params = {
        k: v
        for k, v in kwargs.items()
        if k.endswith("_id") and isinstance(v, int | str)
    }

    # Extract other simple values for logging context
    simple_params = {
        k: v
        for k, v in kwargs.items()
        if (
            not k.startswith("_")
            and not isinstance(v, dict | list | set)
            and k not in id_params
        )
    }

    # Combine contexts with IDs taking precedence
    context = {**simple_params, **id_params}

    return context
