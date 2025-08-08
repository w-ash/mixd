"""Centralized retry logic for API batch processors.

Provides reusable retry decorators and error handling for external API operations,
eliminating duplicate backoff configuration across multiple processors.
"""

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import backoff

from src.config import get_logger

# Get contextual logger
logger = get_logger(__name__).bind(service="retry_wrapper")

# Define type variables
T = TypeVar("T")
R = TypeVar("R")


class RetryWrapper:
    """Centralized retry logic for API operations with configurable backoff strategies."""

    def __init__(
        self,
        retry_count: int,
        retry_base_delay: float,
        retry_max_delay: float,
        logger_instance: Any = None,
    ):
        """Initialize retry wrapper with configuration.
        
        Args:
            retry_count: Max retry attempts per failed operation
            retry_base_delay: Starting delay between retries (seconds)
            retry_max_delay: Maximum delay between retries (seconds)
            logger_instance: Logger for retry events
        """
        self.retry_count = retry_count
        self.retry_base_delay = retry_base_delay
        self.retry_max_delay = retry_max_delay
        self.logger_instance = logger_instance or logger

    def _on_backoff(self, details):
        """Log retry attempt with delay information."""
        wait = details["wait"]
        tries = details["tries"]
        target = details["target"].__name__
        args = details["args"]
        kwargs = details["kwargs"]

        self.logger_instance.warning(
            f"Backing off {target} (attempt {tries})",
            retry_delay=f"{wait:.2f}s",
            args=args,
            kwargs=kwargs,
        )

    def _on_giveup(self, details):
        """Log final failure after all retry attempts exhausted."""
        target = details["target"].__name__
        tries = details["tries"]
        elapsed = details["elapsed"]
        exception = details.get("exception")

        self.logger_instance.error(
            f"All {tries} attempts failed for {target}",
            elapsed_time=f"{elapsed:.2f}s",
            error=str(exception) if exception else "Unknown error",
            error_type=type(exception).__name__ if exception else "Unknown",
        )

    def with_exponential_backoff(
        self,
        func: Callable[..., Awaitable[R]],
    ) -> Callable[..., Awaitable[R]]:
        """Wrap async function with exponential backoff retry logic.
        
        Args:
            func: Async function to wrap with retry logic
            
        Returns:
            Wrapped function with exponential backoff retry behavior
        """
        @backoff.on_exception(
            backoff.expo,
            Exception,  # Catch all exceptions - can be customized for specific error types
            max_tries=self.retry_count + 1,  # +1 because first attempt counts
            max_time=None,  # No time limit, just use max_tries
            factor=self.retry_base_delay,
            max_value=self.retry_max_delay,
            jitter=backoff.full_jitter,
            on_backoff=self._on_backoff,
            on_giveup=self._on_giveup,
        )
        async def wrapped(*args, **kwargs):
            return await func(*args, **kwargs)
        
        return wrapped

    def create_retry_decorator(self):
        """Create a backoff decorator with this wrapper's configuration.
        
        Returns:
            Configured backoff decorator for direct application to functions
        """
        return backoff.on_exception(
            backoff.expo,
            Exception,
            max_tries=self.retry_count + 1,
            max_time=None,
            factor=self.retry_base_delay,
            max_value=self.retry_max_delay,
            jitter=backoff.full_jitter,
            on_backoff=self._on_backoff,
            on_giveup=self._on_giveup,
        )