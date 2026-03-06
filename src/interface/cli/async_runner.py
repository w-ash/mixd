"""Async-to-sync bridge for CLI commands.

Provides the sync wrapper that Typer command handlers use to call async
use cases. FastAPI's web interface won't need this — it's natively async.
"""

# pyright: reportExplicitAny=false
# Legitimate Any: Coroutine[Any,Any,T], Rich/Typer display types

import asyncio
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from src.config import get_logger, settings

logger = get_logger(__name__)


def create_executor_for_connectors() -> ThreadPoolExecutor:
    """Create ThreadPoolExecutor configured for high-concurrency connector operations.

    Returns:
        ThreadPoolExecutor with max_workers set to lastfm_concurrency setting
        for optimal I/O-bound API operations.
    """
    return ThreadPoolExecutor(
        max_workers=settings.api.lastfm.concurrency,
        thread_name_prefix="narada_io",
    )


def run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run coroutine with custom high-concurrency executor.

    Uses ``asyncio.run()`` (Python 3.14+ pattern) with a pre-configured
    thread pool executor for connector I/O operations.

    Args:
        coro: Coroutine to execute

    Returns:
        Result of the coroutine execution
    """

    async def _run_with_executor():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(create_executor_for_connectors())
        logger.debug(
            "Running coroutine with connector executor",
            executor_max_workers=settings.api.lastfm.concurrency,
        )
        return await coro

    return asyncio.run(_run_with_executor())
