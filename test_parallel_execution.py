#!/usr/bin/env python3
"""Simple test to verify parallel execution works with the simplified batch processor."""

import asyncio
import time
from unittest.mock import Mock

from aiolimiter import AsyncLimiter

from src.infrastructure.connectors._shared.api_batch_processor import APIBatchProcessor


async def mock_api_call(item: str) -> str:
    """Mock API call that takes some time."""
    await asyncio.sleep(0.1)  # Simulate API response time
    return f"processed_{item}"


async def test_parallel_execution():
    """Test that batch processor creates tasks in parallel."""
    print("Testing parallel execution with simplified batch processor...")
    
    # Create rate limiter (10 requests per second)
    rate_limiter = AsyncLimiter(max_rate=10.0, time_period=1.0)
    
    # Create batch processor without streaming_tasks parameter
    processor = APIBatchProcessor(
        batch_size=5,
        concurrency_limit=10,
        retry_count=1,
        retry_base_delay=0.1,
        retry_max_delay=1.0,
        request_delay=0.01,
        rate_limiter=rate_limiter,
        logger_instance=Mock(),
    )
    
    # Test items
    items = [f"item_{i}" for i in range(5)]
    
    # Time the execution
    start_time = time.time()
    
    results = await processor.process(
        items=items,
        process_func=mock_api_call,
        progress_callback=None,
        progress_task_name="test_batch",
        progress_description="Testing parallel execution",
    )
    
    end_time = time.time()
    duration = end_time - start_time
    
    print(f"Processed {len(items)} items in {duration:.2f} seconds")
    print(f"Results: {results}")
    
    # With parallel execution, this should take ~0.6 seconds:
    # - Rate limiter spaces calls 0.1 seconds apart (5 calls = 0.4s to start all)
    # - Each call takes 0.1s to complete
    # - Total ~0.5-0.6 seconds
    #
    # With serial execution, this would take ~1.4 seconds:
    # - Each call waits 0.1s for rate limit + 0.1s to complete = 0.2s per call
    # - 5 calls * 0.2s = 1.0s + overhead
    
    if duration < 1.0:
        print("✅ SUCCESS: Parallel execution detected (fast completion)")
        return True
    else:
        print("❌ FAILURE: Serial execution detected (slow completion)")
        return False


if __name__ == "__main__":
    success = asyncio.run(test_parallel_execution())
    exit(0 if success else 1)