"""Request Start Gate - Controls when API requests can BEGIN without limiting concurrency.

This module provides a simple asyncio-based gate that ensures API requests start at
controlled intervals while allowing unlimited concurrent execution once started.

Key features:
- Controls request START timing, not concurrent execution
- Thread-safe using asyncio.Lock
- Works with retry mechanisms (each retry waits for gate approval)
- Simple time-based delay without complex token bucket algorithms
"""

import asyncio
import threading
import time

from attrs import define, field

from src.config import get_logger

# Get contextual logger for gate operations
logger = get_logger(__name__).bind(service="request_start_gate")


@define(slots=True)
class RequestStartGate:
    """Gate that controls when API requests can start.

    Ensures requests begin at controlled intervals (e.g., every 200ms for 5/sec rate limit)
    while allowing unlimited concurrent execution after start approval.

    Thread-safe and works with retry mechanisms - each request start (including retries)
    must wait for gate approval.

    Args:
        delay: Minimum seconds between request starts (e.g., 0.2 for 5/sec limit)
    """

    delay: float
    _lock: asyncio.Lock = field(factory=asyncio.Lock, init=False)
    _next_request_time: float = field(default=0.0, init=False)

    async def wait(self, call_id: str | None = None) -> None:
        """Wait for permission to start a new request.

        First request passes immediately. Subsequent requests wait until
        the configured delay has passed since the previous request start.

        This method allows concurrent waiting - multiple tasks can sleep
        simultaneously without blocking each other.

        Args:
            call_id: Optional call identifier for detailed tracing
        """
        thread_id = threading.get_ident()
        request_start = time.time()
        call_context = {"call_id": call_id} if call_id else {}

        # Create contextual logger for this gate access
        gate_logger = logger.bind(**call_context, thread_id=thread_id)

        gate_logger.debug(
            f"RequestStartGate: Entering gate [call_id={call_id}]",
            gate_entry_time=request_start,
        )

        # Calculate wait time with lock protection (but don't sleep with lock held!)
        wait_time = 0.0
        lock_acquire_start = time.time()
        async with self._lock:
            lock_acquired_time = time.time()
            lock_wait_duration = lock_acquired_time - lock_acquire_start
            now = time.time()

            gate_logger.debug(
                f"RequestStartGate: Acquired lock [call_id={call_id}]",
                lock_wait_duration_ms=round(lock_wait_duration * 1000, 3),
                current_time=now,
                next_allowed=self._next_request_time,
                delay_config=self.delay,
                time_since_next_allowed=round((now - self._next_request_time) * 1000, 3) if self._next_request_time > 0 else "N/A",
                gate_state="first_request" if self._next_request_time == 0.0 else "has_next_time"
            )

            # If this is the first request or enough time has passed
            old_next_request_time = self._next_request_time
            if self._next_request_time == 0.0 or now >= self._next_request_time:
                self._next_request_time = now + self.delay
                gate_logger.info(
                    f"RequestStartGate: IMMEDIATE PASS [call_id={call_id}]",
                    next_slot_reserved=self._next_request_time,
                    wait_time_ms=0.0,
                    is_first_request=old_next_request_time == 0.0,
                    calculation_detail=f"now({now:.3f}) >= next_allowed({old_next_request_time:.3f})"
                )
                return

            # Calculate how long to wait, but don't sleep yet
            wait_time = self._next_request_time - now
            # Reserve the next slot
            old_next_time = self._next_request_time
            self._next_request_time += self.delay

            gate_logger.info(
                f"RequestStartGate: DELAY REQUIRED [call_id={call_id}]",
                wait_time_ms=round(wait_time * 1000, 3),
                old_next_slot=old_next_time,
                new_next_slot=self._next_request_time,
                slot_increment_ms=round(self.delay * 1000, 3),
                calculation_detail=f"wait_time = next_allowed({old_next_time:.3f}) - now({now:.3f}) = {wait_time:.3f}s",
                delay_config_seconds=self.delay,
                current_timestamp=now,
                next_slot_timestamp=old_next_time
            )

        # CRITICAL FIX: Sleep OUTSIDE the lock to allow concurrent delays
        if wait_time > 0:
            sleep_start = time.time()
            gate_logger.debug(
                f"RequestStartGate: Starting sleep [call_id={call_id}]",
                sleep_duration_ms=round(wait_time * 1000, 3),
            )

            await asyncio.sleep(wait_time)

            actual_sleep = time.time() - sleep_start
            total_gate_time = time.time() - request_start

            gate_logger.info(
                f"RequestStartGate: Sleep completed - REQUEST APPROVED [call_id={call_id}]",
                requested_sleep_ms=round(wait_time * 1000, 3),
                actual_sleep_ms=round(actual_sleep * 1000, 3),
                total_gate_duration_ms=round(total_gate_time * 1000, 3),
                sleep_accuracy=f"{round((actual_sleep / wait_time) * 100, 1)}%",
            )
        else:
            gate_logger.info(
                f"RequestStartGate: No sleep needed - REQUEST APPROVED [call_id={call_id}]",
                total_gate_duration_ms=round((time.time() - request_start) * 1000, 3),
            )
